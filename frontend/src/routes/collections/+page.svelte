<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import DeleteIcon from "$lib/components/icons/DeleteIcon.svelte";
  import EditIcon from "$lib/components/icons/EditIcon.svelte";
  import SaveIcon from "$lib/components/icons/SaveIcon.svelte";
  import {
    createCollection,
    deleteCollection,
    getCollections,
    getSongs,
    getUser,
    updateCollection,
    updateSongCollections,
  } from "$lib/api.js";
  import { showToast } from "$lib/toasts.js";

  let me = null;
  let collections = [];
  let songs = [];
  let collectionForm = { name: "", notes: "" };
  let showCreateCollectionModal = false;
  let showEditCollectionModal = false;
  let activeCollection = null;
  let editCollectionForm = { name: "", notes: "" };
  let songSearch = "";

  $: collectionSongs = activeCollection
    ? songs
        .filter((song) => (song.collections ?? []).some((collection) => collection.id === activeCollection.id))
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  $: searchableSongs = activeCollection
    ? songs
        .filter((song) => !(song.collections ?? []).some((collection) => collection.id === activeCollection.id))
        .filter((song) => normalizedSongLabel(song).includes(songSearch.trim().toLowerCase()))
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  function canManageCollections() {
    return me?.user_group === "admin" || me?.user_group === "editor";
  }

  function normalizeCollections(items) {
    return items.map((collection) => ({ ...collection, notes: collection.notes ?? "" }));
  }

  function normalizeSongs(items) {
    return items.map((song) => ({ ...song, collections: song.collections ?? [] }));
  }

  function normalizedSongLabel(song) {
    return [song.name, song.composer, song.arrangement, song.notes]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
  }

  function songLabel(song) {
    return song.composer ? `${song.name} (${song.composer})` : song.name;
  }

  function openCreateCollectionModal() {
    collectionForm = { name: "", notes: "" };
    showCreateCollectionModal = true;
  }

  function closeCreateCollectionModal() {
    showCreateCollectionModal = false;
    collectionForm = { name: "", notes: "" };
  }

  async function loadAll() {
    try {
      me = await getUser();
      if (!canManageCollections()) {
        goto("/songs");
        return;
      }
      const [loadedCollections, loadedSongs] = await Promise.all([getCollections(), getSongs()]);
      collections = normalizeCollections(loadedCollections);
      songs = normalizeSongs(loadedSongs);
    } catch (err) {
      showToast(err.message, "error");
      goto("/");
    }
  }

  async function createNewCollection() {
    if (!collectionForm.name.trim()) {
      showToast("Bitte einen Sammlungsnamen eingeben.", "error");
      return;
    }
    try {
      await createCollection({
        name: collectionForm.name.trim(),
        notes: collectionForm.notes.trim() || null,
      });
      closeCreateCollectionModal();
      collections = normalizeCollections(await getCollections());
      showToast("Sammlung angelegt.", "ok");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function openEditCollectionModal(collection) {
    activeCollection = collection;
    editCollectionForm = { name: collection.name ?? "", notes: collection.notes ?? "" };
    songSearch = "";
    showEditCollectionModal = true;
  }

  function closeEditCollectionModal() {
    showEditCollectionModal = false;
    activeCollection = null;
    editCollectionForm = { name: "", notes: "" };
    songSearch = "";
  }

  function updateSongInList(updatedSong) {
    songs = songs.map((song) => (song.id === updatedSong.id ? { ...updatedSong, collections: updatedSong.collections ?? [] } : song));
  }

  async function saveActiveCollectionOnBlur() {
    if (!activeCollection) return;
    const trimmedName = editCollectionForm.name.trim();
    if (!trimmedName) {
      showToast("Bitte einen Sammlungsnamen eingeben.", "error");
      editCollectionForm = { name: activeCollection.name ?? "", notes: activeCollection.notes ?? "" };
      return;
    }

    const trimmedNotes = editCollectionForm.notes.trim();
    const nextName = trimmedName;
    const nextNotes = trimmedNotes || "";
    const currentName = activeCollection.name ?? "";
    const currentNotes = activeCollection.notes ?? "";
    if (nextName === currentName && nextNotes === currentNotes) return;

    try {
      await updateCollection(activeCollection.id, {
        name: trimmedName,
        notes: trimmedNotes || null,
      });
      collections = normalizeCollections(await getCollections());
      activeCollection = collections.find((collection) => collection.id === activeCollection.id) ?? activeCollection;
      editCollectionForm = { name: activeCollection.name ?? "", notes: activeCollection.notes ?? "" };
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function addSongToActiveCollection(song) {
    if (!activeCollection) return;
    try {
      const collectionIds = [
        ...(song.collections ?? []).map((collection) => collection.id).filter((id) => id !== activeCollection.id),
        activeCollection.id,
      ];
      const updatedSong = await updateSongCollections(song.id, collectionIds);
      updateSongInList(updatedSong);
      showToast("Stück zur Sammlung hinzugefügt.", "ok");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function removeSongFromActiveCollection(song) {
    if (!activeCollection) return;
    try {
      const collectionIds = (song.collections ?? [])
        .map((collection) => collection.id)
        .filter((id) => id !== activeCollection.id);
      const updatedSong = await updateSongCollections(song.id, collectionIds);
      updateSongInList(updatedSong);
      showToast("Stück aus Sammlung entfernt.", "ok");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function removeCollection(collection) {
    if (!confirm(`Sammlung "${collection.name}" wirklich löschen?`)) return;
    try {
      await deleteCollection(collection.id);
      collections = normalizeCollections(await getCollections());
      showToast("Sammlung gelöscht.", "ok");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  onMount(loadAll);
</script>

<style>
  .collections-header {
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }

  .collections-header h2 {
    margin: 0;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 1rem;
  }

  .modal {
    width: min(760px, 100%);
    background: white;
    border-radius: 12px;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.2);
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .modal-subgrid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }

  .list-card {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-height: 240px;
  }

  .list-card ul {
    margin: 0;
    padding-left: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .list-card li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .collection-link {
    border: none;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }

</style>

{#if me && canManageCollections()}
  <section class="card">
    <div class="row collections-header">
      <h2>Sammlungen verwalten</h2>
      <button
        class="sm"
        onclick={openCreateCollectionModal}
        aria-label="Neue Sammlung anlegen"
        title="Neue Sammlung anlegen"
      >
        +
      </button>
    </div>

    {#if collections.length === 0}
      <p>Noch keine Sammlungen vorhanden.</p>
    {:else}
      <div class="table-wrap" style="margin-top: 0.5rem;">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Notizen</th>
              <th>Stücke</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody>
            {#each collections as collection}
              <tr>
                <td>
                  <button
                    class="collection-link"
                    onclick={() => openEditCollectionModal(collection)}
                    aria-label={`Sammlung ${collection.name} bearbeiten`}
                  >
                    {collection.name}
                  </button>
                </td>
                <td>{collection.notes || "-"}</td>
                <td>{songs.filter((song) => (song.collections ?? []).some((item) => item.id === collection.id)).length}</td>
                <td>
                  <div class="row">
                    <button
                      class="secondary"
                      onclick={() => openEditCollectionModal(collection)}
                      aria-label="Sammlung bearbeiten"
                      title="Sammlung bearbeiten"
                    >
                      <EditIcon />
                    </button>
                    <button
                      class="warn"
                      onclick={() => removeCollection(collection)}
                      aria-label="Sammlung löschen"
                      title="Sammlung löschen"
                    >
                      <DeleteIcon />
                    </button>
                  </div>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  {#if showCreateCollectionModal}
    <div
      class="modal-backdrop"
      role="button"
      tabindex="0"
      onclick={closeCreateCollectionModal}
      onkeydown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Escape") {
          event.preventDefault();
          closeCreateCollectionModal();
        }
      }}
    >
      <div
        class="modal"
        role="dialog"
        tabindex="0"
        aria-modal="true"
        aria-labelledby="create-collection-modal-title"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeCreateCollectionModal();
          }
        }}
      >
        <h3 id="create-collection-modal-title" style="margin: 0;">Neue Sammlung anlegen</h3>
        <label>
          <span>Name</span>
          <input placeholder="Neue Sammlung" bind:value={collectionForm.name} />
        </label>
        <label>
          <span>Notizen</span>
          <input placeholder="Notizen" bind:value={collectionForm.notes} />
        </label>
        <div class="modal-actions">
          <button class="secondary" onclick={closeCreateCollectionModal}>Abbrechen</button>
          <button onclick={createNewCollection}><SaveIcon /></button>
        </div>
      </div>
    </div>
  {/if}

  {#if showEditCollectionModal && activeCollection}
    <div
      class="modal-backdrop"
      role="button"
      tabindex="0"
      onclick={closeEditCollectionModal}
      onkeydown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Escape") {
          event.preventDefault();
          closeEditCollectionModal();
        }
      }}
    >
      <div
        class="modal"
        role="dialog"
        tabindex="0"
        aria-modal="true"
        aria-labelledby="edit-collection-modal-title"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeEditCollectionModal();
          }
        }}
      >
        <h3 id="edit-collection-modal-title" style="margin: 0;">Sammlung bearbeiten</h3>
        <label>
          <span>Name</span>
          <input bind:value={editCollectionForm.name} onblur={saveActiveCollectionOnBlur} />
        </label>
        <label>
          <span>Notizen</span>
          <input bind:value={editCollectionForm.notes} onblur={saveActiveCollectionOnBlur} />
        </label>

        <div class="modal-subgrid">
          <div class="list-card">
            <h4 style="margin: 0;">Stücke in dieser Sammlung</h4>
            {#if collectionSongs.length === 0}
              <p style="margin: 0;">Noch keine Stücke in dieser Sammlung.</p>
            {:else}
              <ul>
                {#each collectionSongs as song}
                  <li>
                    <span>{songLabel(song)}</span>
                    <button class="warn sm" onclick={() => removeSongFromActiveCollection(song)}>Entfernen</button>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>

          <div class="list-card">
            <h4 style="margin: 0;">Stück hinzufügen</h4>
            <input placeholder="Nach Stück suchen" bind:value={songSearch} />
            {#if searchableSongs.length === 0}
              <p style="margin: 0;">Keine passenden Stücke gefunden.</p>
            {:else}
              <ul>
                {#each searchableSongs as song}
                  <li>
                    <span>{songLabel(song)}</span>
                    <button class="secondary sm" onclick={() => addSongToActiveCollection(song)}>Hinzufügen</button>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        </div>
        <div class="modal-actions">
          <button type="button" class="secondary" onclick={closeEditCollectionModal} aria-label="Schließen" title="Schließen">✕</button>
        </div>
      </div>
    </div>
  {/if}
{/if}
