<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { createInstrument, deleteInstrument, getInstruments, getUser, updateInstrument } from "$lib/api.js";

  let me = null;
  let error = "";
  let ok = "";
  let instruments = [];
  let selectedInstrumentIds = [];
  let editingInstrumentId = null;
  let showInstrumentModal = false;
  let instrumentForm = { instrument_name: "", instrument_tuning: "", notes: "" };

  function canManageInstruments() {
    return me?.user_group === "admin" || me?.user_group === "editor";
  }

  function resetInstrumentForm() {
    editingInstrumentId = null;
    showInstrumentModal = false;
    instrumentForm = { instrument_name: "", instrument_tuning: "", notes: "" };
  }

  async function loadAll() {
    error = "";
    ok = "";
    try {
      me = await getUser();
      if (!canManageInstruments()) {
        goto("/songs");
        return;
      }
      instruments = await getInstruments();
      selectedInstrumentIds = selectedInstrumentIds.filter((instrumentId) =>
        instruments.some((instrument) => Number(instrument.id) === Number(instrumentId))
      );
    } catch (err) {
      error = err.message;
      goto("/");
    }
  }

  function openCreateInstrumentModal() {
    editingInstrumentId = null;
    instrumentForm = { instrument_name: "", instrument_tuning: "", notes: "" };
    showInstrumentModal = true;
    error = "";
    ok = "";
  }

  function openEditInstrumentModal(instrument) {
    editingInstrumentId = instrument.id;
    instrumentForm = {
      instrument_name: instrument.instrument_name,
      instrument_tuning: instrument.instrument_tuning,
      notes: instrument.notes ?? "",
    };
    showInstrumentModal = true;
    error = "";
    ok = "";
  }

  async function saveInstrument() {
    error = "";
    ok = "";
    if (!instrumentForm.instrument_name.trim()) {
      error = "Bitte einen Instrumentnamen eingeben.";
      return;
    }
    if (!instrumentForm.instrument_tuning.trim()) {
      error = "Bitte eine Stimmung eingeben.";
      return;
    }
    try {
      const payload = {
        instrument_name: instrumentForm.instrument_name.trim(),
        instrument_tuning: instrumentForm.instrument_tuning.trim(),
        notes: instrumentForm.notes.trim() || null,
      };

      if (editingInstrumentId !== null) {
        await updateInstrument(editingInstrumentId, payload);
        ok = "Instrument aktualisiert.";
      } else {
        await createInstrument(payload);
        ok = "Instrument angelegt.";
      }

      resetInstrumentForm();
      await loadAll();
    } catch (err) {
      error = err.message;
    }
  }

  async function removeInstrument(instrument) {
    error = "";
    ok = "";
    if (!confirm(`Instrument "${instrument.instrument_name}" wirklich löschen?`)) return;
    try {
      await deleteInstrument(instrument.id);
      selectedInstrumentIds = selectedInstrumentIds.filter((instrumentId) => Number(instrumentId) !== Number(instrument.id));
      if (editingInstrumentId === instrument.id) {
        resetInstrumentForm();
      }
      await loadAll();
      ok = "Instrument gelöscht.";
    } catch (err) {
      error = err.message;
    }
  }

  function setAllInstrumentSelection(checked) {
    selectedInstrumentIds = checked ? instruments.map((instrument) => instrument.id) : [];
  }

  async function removeSelectedInstruments() {
    error = "";
    ok = "";
    if (selectedInstrumentIds.length === 0) {
      error = "Bitte mindestens ein Instrument auswählen.";
      return;
    }
    if (!confirm(`${selectedInstrumentIds.length} Instrument(e) wirklich löschen?`)) return;

    let deletedCount = 0;
    const failed = [];
    for (const instrumentId of selectedInstrumentIds) {
      const instrument = instruments.find((item) => Number(item.id) === Number(instrumentId));
      try {
        await deleteInstrument(Number(instrumentId));
        deletedCount += 1;
      } catch (err) {
        failed.push(`${instrument?.instrument_name ?? instrumentId}: ${err.message}`);
      }
    }

    await loadAll();
    selectedInstrumentIds = [];

    if (failed.length === 0) {
      ok = `${deletedCount} Instrument(e) gelöscht.`;
      return;
    }
    if (deletedCount > 0) {
      ok = `${deletedCount} Instrument(e) gelöscht.`;
    }
    error = `Konnte ${failed.length} Instrument(e) nicht löschen: ${failed.join(" | ")}`;
  }

  onMount(loadAll);
</script>

{#if error}
  <p class="error">{error}</p>
{/if}
{#if ok}
  <p class="ok">{ok}</p>
{/if}

{#if me && canManageInstruments()}
  <section class="card">
    <div class="toolbar">
      <h2>Instrumente</h2>
      <button class="primary" onclick={openCreateInstrumentModal} aria-label="Neues Instrument anlegen">＋</button>
    </div>

    {#if instruments.length === 0}
      <p>Keine Instrumente vorhanden.</p>
    {:else}
      <p>
        <button class="warn" onclick={removeSelectedInstruments}>Ausgewählte löschen</button>
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <label style="font-weight: normal;">
                  <input
                    type="checkbox"
                    checked={instruments.length > 0 && instruments.every((instrument) => selectedInstrumentIds.includes(instrument.id))}
                    onchange={(event) => setAllInstrumentSelection(event.currentTarget.checked)}
                  />
                  Alle
                </label>
              </th>
              <th>Name</th>
              <th>Stimmung</th>
              <th>Notizen</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody>
            {#each instruments as instrument}
              <tr>
                <td>
                  <input type="checkbox" bind:group={selectedInstrumentIds} value={instrument.id} />
                </td>
                <td>{instrument.instrument_name}</td>
                <td>{instrument.instrument_tuning}</td>
                <td>{instrument.notes ?? "-"}</td>
                <td>
                  <button class="secondary" onclick={() => openEditInstrumentModal(instrument)}>Bearbeiten</button>
                  <button class="warn" onclick={() => removeInstrument(instrument)}>Löschen</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  {#if showInstrumentModal}
    <div
      class="modal-backdrop"
      role="button"
      tabindex="0"
      onclick={resetInstrumentForm}
      onkeydown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Escape") {
          event.preventDefault();
          resetInstrumentForm();
        }
      }}
    >
      <div
        class="modal"
        role="dialog"
        tabindex="0"
        aria-modal="true"
        aria-labelledby="instrument-modal-title"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            resetInstrumentForm();
          }
        }}
      >
        <h3 id="instrument-modal-title">{editingInstrumentId === null ? "Instrument anlegen" : "Instrument bearbeiten"}</h3>
        <div class="field-row">
          <label>
            <span>Instrument</span>
            <input bind:value={instrumentForm.instrument_name} />
          </label>
          <label>
            <span>Stimmung</span>
            <input bind:value={instrumentForm.instrument_tuning} />
          </label>
        </div>
        <label>
          <span>Notizen</span>
          <input bind:value={instrumentForm.notes} />
        </label>
        <div class="modal-actions">
          <button class="secondary" onclick={resetInstrumentForm}>Abbrechen</button>
          <button class="primary" onclick={saveInstrument}>{editingInstrumentId === null ? "Anlegen" : "Speichern"}</button>
        </div>
      </div>
    </div>
  {/if}
{/if}

<style>
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .primary,
  .secondary,
  .warn {
    cursor: pointer;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.55);
    display: grid;
    place-items: center;
    z-index: 1000;
    padding: 1rem;
  }

  .modal {
    width: min(560px, 100%);
    background: white;
    border-radius: 12px;
    padding: 1.5rem;
    box-shadow: 0 18px 50px rgba(15, 23, 42, 0.2);
  }

  .field-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
  }

  .modal label {
    display: block;
    margin-bottom: 1rem;
  }

  .modal label span {
    display: block;
    margin-bottom: 0.4rem;
    font-weight: 600;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1rem;
  }
</style>
