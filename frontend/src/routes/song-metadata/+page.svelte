<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { createSongMetadata, deleteSongMetadata, getSongMetadata, getUser, updateSongMetadata } from "$lib/api.js";
  import { showToast } from "$lib/toasts.js";
  import CollectionsManager from "$lib/components/CollectionsManager.svelte";

  const categories = [
    { key: "ensembles", label: "Besetzungen", field: "name", valueLabel: "Name" },
    { key: "kinds", label: "Arten des Stücks", field: "name", valueLabel: "Name" },
    { key: "difficulty_levels", label: "Schwierigkeitsgrade", field: "name", valueLabel: "Name" },
    { key: "rhythms", label: "Rhythmen", field: "name", valueLabel: "Name" },
    { key: "physical_storages", label: "Aufbewahrungsorte", field: "storage_path", valueLabel: "Aufbewahrungsort" },
  ];

  let me = null;
  let metadata = {};
  let newItems = Object.fromEntries(categories.map((category) => [category.key, { value: "", notes: "" }]));

  function canManageMetadata() {
    return me?.user_group === "admin" || me?.user_group === "editor";
  }

  function normalizeItems(category, loaded) {
    const descriptor = categories.find((item) => item.key === category);
    return (loaded ?? []).map((item) => ({
      ...item,
      [descriptor.field]: item[descriptor.field] ?? "",
      notes: item.notes ?? "",
    }));
  }

  async function loadAll() {
    try {
      me = await getUser();
      if (!canManageMetadata()) {
        goto("/songs");
        return;
      }
      const loaded = await getSongMetadata();
      metadata = Object.fromEntries(
        categories.map((category) => [category.key, normalizeItems(category.key, loaded[category.key])])
      );
    } catch (error) {
      showToast(error.message, "error");
      goto("/");
    }
  }

  function payloadFor(category, item) {
    return {
      [category.field]: item[category.field]?.trim() || null,
      notes: item.notes?.trim() || null,
    };
  }

  async function addItem(category) {
    const form = newItems[category.key];
    const value = form.value.trim();
    if (!value && category.key !== "physical_storages") {
      showToast("Bitte einen Namen eingeben.", "error");
      return;
    }
    if (!value && !form.notes.trim()) {
      showToast("Bitte einen Aufbewahrungsort oder Notizen eingeben.", "error");
      return;
    }
    try {
      await createSongMetadata(category.key, {
        [category.field]: value || null,
        notes: form.notes.trim() || null,
      });
      newItems = { ...newItems, [category.key]: { value: "", notes: "" } };
      await loadMetadata();
      showToast("Eintrag angelegt.", "ok");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function loadMetadata() {
    const loaded = await getSongMetadata();
    metadata = Object.fromEntries(
      categories.map((category) => [category.key, normalizeItems(category.key, loaded[category.key])])
    );
  }

  async function saveItem(category, item) {
    if (category.key !== "physical_storages" && !item[category.field].trim()) {
      showToast("Bitte einen Namen eingeben.", "error");
      return;
    }
    if (category.key === "physical_storages" && !item[category.field].trim() && !item.notes.trim()) {
      showToast("Bitte einen Aufbewahrungsort oder Notizen eingeben.", "error");
      return;
    }
    try {
      await updateSongMetadata(category.key, item.id, payloadFor(category, item));
      await loadMetadata();
      showToast("Eintrag gespeichert.", "ok");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function removeItem(category, item) {
    const label = item[category.field] || item.notes || `#${item.id}`;
    if (!confirm(`Eintrag "${label}" wirklich löschen?`)) return;
    try {
      await deleteSongMetadata(category.key, item.id);
      await loadMetadata();
      showToast("Eintrag gelöscht.", "ok");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  onMount(loadAll);
</script>

{#if me && canManageMetadata()}
  <h1>Stammdaten & Sammlungen</h1>
  <section class="card">
    <p>Hier verwaltest du die Auswahllisten und Sammlungen, die in den Stückdetails verwendet werden.</p>
  </section>

  {#each categories as category (category.key)}
    <section class="card">
      <h2>{category.label}</h2>
      <form class="new-item" onsubmit={(event) => { event.preventDefault(); addItem(category); }}>
        <label>
          <span>{category.valueLabel}</span>
          <input
            bind:value={newItems[category.key].value}
            placeholder={category.key === "physical_storages" ? "Optional" : "Neuer Eintrag"}
          />
        </label>
        <label>
          <span>Notizen</span>
          <input bind:value={newItems[category.key].notes} placeholder="Optional" />
        </label>
        <button type="submit">Hinzufügen</button>
      </form>

      {#if (metadata[category.key] ?? []).length === 0}
        <p>Keine Einträge vorhanden.</p>
      {:else}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{category.valueLabel}</th>
                <th>Notizen</th>
                <th>Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {#each metadata[category.key] as item (item.id)}
                <tr>
                  <td><input bind:value={item[category.field]} /></td>
                  <td><input bind:value={item.notes} /></td>
                  <td>
                    <div class="row">
                      <button class="secondary" onclick={() => saveItem(category, item)}>Speichern</button>
                      <button class="warn" onclick={() => removeItem(category, item)}>Löschen</button>
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {/each}

  <CollectionsManager />
{/if}

<style>
  .new-item {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
    align-items: end;
    gap: 0.6rem;
    margin: 0.75rem 0;
  }

  .new-item label span {
    display: block;
    margin-bottom: 0.25rem;
  }

  @media (max-width: 640px) {
    .new-item {
      grid-template-columns: 1fr;
    }
  }
</style>
