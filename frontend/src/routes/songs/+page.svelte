<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import {
    createCollection,
    deleteCollection,
    getCollections,
    getInstruments,
    getScores,
    getStorageStatus,
    getScoreDocumentUrl,
    getSongs,
    getUser,
    rescrapeStorage,
    updateCollection,
    updateSongCollections,
    uploadScorePdf,
  } from "$lib/api.js";

  let me = null;
  let error = "";
  let ok = "";
  let songs = [];
  let collections = [];
  let instruments = [];
  let storageStatus = null;
  let songFilter = "";
  let activeSong = null;
  let activeSongScores = [];
  let scoresModal = null;
  let modalTab = "scores";
  let selectedSongCollectionIds = [];
  let uploadForm = {
    instrument_id: "",
    use_new_instrument: false,
    new_instrument_name: "",
    new_instrument_tuning: "C",
    notes: "",
    file: null,
  };
  let isDragActive = false;
  let collectionForm = { name: "", notes: "" };

  function normalizeCollections(items) {
    return items.map((collection) => ({ ...collection, notes: collection.notes ?? "" }));
  }

  async function loadAll() {
    error = "";
    ok = "";
    try {
      me = await getUser();
      const [loadedSongs, loadedInstruments, loadedCollections] = await Promise.all([
        getSongs(),
        getInstruments(),
        getCollections(),
      ]);
      songs = loadedSongs;
      instruments = loadedInstruments;
      collections = normalizeCollections(loadedCollections);
      if (me.user_group === "admin") {
        storageStatus = await getStorageStatus();
      }
      if (instruments.length > 0) {
        uploadForm.instrument_id = String(instruments[0].id);
      }
    } catch (err) {
      error = err.message;
      goto("/");
    }
  }

  function normalized(value) {
    return (value ?? "").toString().toLowerCase();
  }

  $: filteredSongs = songs.filter((song) => {
    const search = normalized(songFilter).trim();
    if (!search) return true;
    return (
      normalized(song.name).includes(search) ||
      normalized(song.tune).includes(search) ||
      normalized(song.composer).includes(search) ||
      normalized(song.arrangement).includes(search) ||
      normalized(song.notes).includes(search) ||
      normalized(song.collections?.map((collection) => collection.name).join(" ")).includes(search)
    );
  });

  function collectionNames(song) {
    return song.collections?.map((collection) => collection.name).join(", ") || "-";
  }

  function isPdfStoragePath(path) {
    return normalized(path).endsWith(".pdf");
  }

  async function openScoresModal(song) {
    error = "";
    ok = "";
    try {
      activeSong = song;
      modalTab = "scores";
      selectedSongCollectionIds = (song.collections ?? []).map((collection) => collection.id);
      const scores = await getScores(song.id);
      activeSongScores = scores.filter((score) => isPdfStoragePath(score.storage_path));
      scoresModal.showModal();
    } catch (err) {
      error = err.message;
    }
  }

  function closeScoresModal() {
    if (scoresModal?.open) {
      scoresModal.close();
    }
    activeSong = null;
    activeSongScores = [];
    selectedSongCollectionIds = [];
    uploadForm = {
      instrument_id: instruments.length > 0 ? String(instruments[0].id) : "",
      use_new_instrument: false,
      new_instrument_name: "",
      new_instrument_tuning: "C",
      notes: "",
      file: null,
    };
    isDragActive = false;
  }

  async function refreshActiveSongScores() {
    if (!activeSong) return;
    const scores = await getScores(activeSong.id);
    activeSongScores = scores.filter((score) => isPdfStoragePath(score.storage_path));
  }

  async function refreshSongsAndCollections() {
    const [loadedSongs, loadedCollections] = await Promise.all([getSongs(), getCollections()]);
    songs = loadedSongs;
    collections = normalizeCollections(loadedCollections);
    if (me?.user_group === "admin") {
      storageStatus = await getStorageStatus();
    }
    if (activeSong) {
      activeSong = songs.find((song) => song.id === activeSong.id) ?? activeSong;
      selectedSongCollectionIds = (activeSong.collections ?? []).map((collection) => collection.id);
    }
  }

  function storageAlertText() {
    if (!storageStatus?.has_changes) return "";
    const parts = [];
    if (storageStatus.added_files.length > 0) parts.push(`${storageStatus.added_files.length} neue Datei(en)`);
    if (storageStatus.removed_files.length > 0) parts.push(`${storageStatus.removed_files.length} fehlende Datei(en)`);
    if (storageStatus.changed_files.length > 0) parts.push(`${storageStatus.changed_files.length} geänderte Datei(en)`);
    if (storageStatus.unmatched_files.length > 0) parts.push(`${storageStatus.unmatched_files.length} nicht zuordenbare Datei(en)`);
    return parts.join(", ");
  }

  async function uploadPdf() {
    error = "";
    ok = "";
    if (me?.user_group !== "admin") {
      error = "Nur Admins dürfen PDFs hochladen.";
      return;
    }
    if (!activeSong) {
      error = "Kein Stück ausgewählt.";
      return;
    }
    if (uploadForm.use_new_instrument) {
      if (!uploadForm.new_instrument_name.trim()) {
        error = "Bitte einen Namen für die neue Stimme eingeben.";
        return;
      }
    } else if (!uploadForm.instrument_id) {
      error = "Bitte eine Stimme wählen.";
      return;
    }
    if (!uploadForm.file) {
      error = "Bitte eine PDF-Datei auswählen.";
      return;
    }
    if (!isPdfStoragePath(uploadForm.file.name)) {
      error = "Es sind nur PDF-Dateien erlaubt.";
      return;
    }

    try {
      await uploadScorePdf(
        activeSong.id,
        uploadForm.file,
        {
          instrumentId: uploadForm.use_new_instrument ? null : Number(uploadForm.instrument_id),
          instrumentName: uploadForm.use_new_instrument ? uploadForm.new_instrument_name.trim() : null,
          instrumentTuning: uploadForm.use_new_instrument ? uploadForm.new_instrument_tuning.trim() || "C" : null,
          notes: uploadForm.notes?.trim() ? uploadForm.notes : null,
        }
      );
      ok = "PDF wurde hochgeladen.";
      uploadForm.file = null;
      uploadForm.notes = "";
      uploadForm.use_new_instrument = false;
      uploadForm.new_instrument_name = "";
      uploadForm.new_instrument_tuning = "C";
      instruments = await getInstruments();
      uploadForm.instrument_id = instruments.length > 0 ? String(instruments[0].id) : "";
      modalTab = "scores";
      await refreshActiveSongScores();
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  async function saveSongCollections() {
    error = "";
    ok = "";
    if (me?.user_group !== "admin" || !activeSong) {
      return;
    }
    try {
      await updateSongCollections(activeSong.id, selectedSongCollectionIds);
      ok = "Sammlungen gespeichert.";
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  async function runStorageRescrape() {
    error = "";
    ok = "";
    try {
      const result = await rescrapeStorage();
      storageStatus = result;
      ok = `Scrape abgeschlossen: ${result.created_scores} neu, ${result.updated_scores} aktualisiert, ${result.removed_scores} entfernt.`;
      await refreshSongsAndCollections();
      await refreshActiveSongScores();
    } catch (err) {
      error = err.message;
    }
  }

  async function createNewCollection() {
    error = "";
    ok = "";
    if (!collectionForm.name.trim()) {
      error = "Bitte einen Sammlungsnamen eingeben.";
      return;
    }
    try {
      const created = await createCollection({
        name: collectionForm.name.trim(),
        notes: collectionForm.notes.trim() || null,
      });
      collectionForm = { name: "", notes: "" };
      await refreshSongsAndCollections();
      if (activeSong) {
        selectedSongCollectionIds = [...selectedSongCollectionIds, created.id];
      }
      ok = "Sammlung angelegt.";
    } catch (err) {
      error = err.message;
    }
  }

  async function saveCollection(collection) {
    error = "";
    ok = "";
    try {
      await updateCollection(collection.id, {
        name: collection.name,
        notes: collection.notes || null,
      });
      ok = "Sammlung aktualisiert.";
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  async function removeCollection(collection) {
    error = "";
    ok = "";
    try {
      await deleteCollection(collection.id);
      ok = "Sammlung gelöscht.";
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  onMount(loadAll);
  function selectUploadFile(file) {
    if (!file) return;
    if (!isPdfStoragePath(file.name)) {
      error = "Es sind nur PDF-Dateien erlaubt.";
      return;
    }
    uploadForm.file = file;
    error = "";
  }

  function handleFileInputChange(event) {
    selectUploadFile(event.currentTarget.files?.[0] ?? null);
  }

  function handleDrop(event) {
    event.preventDefault();
    isDragActive = false;
    selectUploadFile(event.dataTransfer?.files?.[0] ?? null);
  }
</script>

<style>
  dialog.card {
    width: min(1200px, 96vw);
    max-width: 96vw;
    min-height: 70vh;
    max-height: 90vh;
    overflow: auto;
  }

  .modal-tabs {
    display: flex;
    gap: 0.5rem;
    margin: 1rem 0;
  }

  .tab-active {
    background: var(--color-primary-600);
    color: var(--color-primary-contrast-600);
  }

  .drop-zone {
    border: 2px dashed var(--color-surface-300);
    border-radius: var(--radius-base, 0.375rem);
    padding: 1rem;
    background: var(--color-surface-50);
    transition: border-color 0.15s ease-in-out, background 0.15s ease-in-out;
  }

  .drop-zone.active {
    border-color: var(--color-primary-400);
    background: var(--color-primary-50);
  }

  .collection-chip {
    display: inline-block;
    padding: 0.15rem 0.45rem;
    margin: 0 0.35rem 0.35rem 0;
    border-radius: 999px;
    background: var(--color-primary-50);
    color: var(--color-primary-700);
    font-size: 0.85rem;
  }

  .storage-warning {
    border: 2px solid var(--color-warning-500);
    background: var(--color-warning-50);
  }
</style>

{#if error}
  <p class="error">{error}</p>
{/if}
{#if ok}
  <p class="ok">{ok}</p>
{/if}

{#if me}
  {#if me.user_group === "admin" && storageStatus?.has_changes}
    <section class="card storage-warning">
      <h2 style="margin-top: 0;">Dateisystem-Abweichung erkannt</h2>
      <p>Der Dateibaum passt nicht mehr zum in der Datenbank abgebildeten Stand.</p>
      <p><strong>Details:</strong> {storageAlertText()}</p>
      <div class="row">
        <button onclick={runStorageRescrape}>Jetzt neu scrapen</button>
      </div>
    </section>
  {/if}

  <section class="card">
    <h2>Stücke</h2>
    <p>
      <input
        placeholder="Filtern (Name, Tonart, Komponist, Arrangement, Notizen)"
        bind:value={songFilter}
        style="width: 100%;"
      />
    </p>
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Name</th>
          <th>Tonart</th>
          <th>Komponist</th>
          <th>Arrangement</th>
          <th>Sammlungen</th>
          <th>Länge</th>
          <th>Notizen</th>
        </tr>
      </thead>
      <tbody>
        {#each filteredSongs as song}
          <tr onclick={() => openScoresModal(song)} style="cursor: pointer;">
            <td>{song.id}</td>
            <td>{song.name}</td>
            <td>{song.tune ?? "-"}</td>
            <td>{song.composer ?? "-"}</td>
            <td>{song.arrangement ?? "-"}</td>
            <td>{collectionNames(song)}</td>
            <td>{song.length ?? "-"}</td>
            <td>{song.notes ?? "-"}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </section>

  <dialog bind:this={scoresModal} class="card">
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h3 style="margin: 0;">Stimmen für {activeSong?.name}</h3>
      <button class="secondary" onclick={closeScoresModal}>Schließen</button>
    </div>
    <div class="modal-tabs">
      <button class={modalTab === "scores" ? "tab-active" : "secondary"} onclick={() => (modalTab = "scores")}
        >Verfügbare Stimmen</button
      >
      {#if me.user_group === "admin"}
        <button class={modalTab === "upload" ? "tab-active" : "secondary"} onclick={() => (modalTab = "upload")}
          >PDF hochladen</button
        >
        <button
          class={modalTab === "collections" ? "tab-active" : "secondary"}
          onclick={() => (modalTab = "collections")}
        >
          Sammlungen
        </button>
      {/if}
    </div>

    {#if modalTab === "scores"}
      {#if activeSongScores.length === 0}
        <p>Keine PDF-Stimmen für dieses Stück verfügbar.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th>Stimme</th>
              <th>Pfad</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody>
            {#each activeSongScores as score}
              <tr>
                <td>{score.instrument_name} ({score.instrument_tuning ?? "-"})</td>
                <td>{score.storage_path}</td>
                <td>
                  <a href={getScoreDocumentUrl(score.id)} target="_blank" rel="noopener noreferrer">
                    PDF öffnen
                  </a>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else if modalTab === "upload" && me.user_group === "admin"}
      <div class="card">
        <div class="row">
          <div style="flex: 1 1 280px;">
            <label for="upload-instrument">Stimme</label>
            <p style="margin: 0 0 0.5rem 0;">
              <label style="font-weight: normal;">
                <input type="checkbox" bind:checked={uploadForm.use_new_instrument} />
                Neue Stimme eingeben
              </label>
            </p>
            {#if uploadForm.use_new_instrument}
              <input placeholder="Neue Stimme (z. B. 4. Klarinette)" bind:value={uploadForm.new_instrument_name} />
              <input
                placeholder="Stimmung (z. B. Bb)"
                bind:value={uploadForm.new_instrument_tuning}
                style="margin-top: 0.5rem;"
              />
            {:else}
              <select id="upload-instrument" bind:value={uploadForm.instrument_id}>
                {#each instruments as instrument}
                  <option value={instrument.id}>{instrument.instrument_name} ({instrument.instrument_tuning})</option>
                {/each}
              </select>
            {/if}
          </div>
          <div
            style="flex: 2 1 420px;"
            class={`drop-zone ${isDragActive ? "active" : ""}`}
            role="region"
            aria-label="PDF Drag-and-Drop Upload-Bereich"
            ondragenter={() => (isDragActive = true)}
            ondragleave={() => (isDragActive = false)}
            ondragover={(event) => event.preventDefault()}
            ondrop={handleDrop}
          >
            <label for="upload-file">PDF-Datei</label>
            <input
              id="upload-file"
              type="file"
              accept=".pdf,application/pdf"
              onchange={handleFileInputChange}
            />
            <p style="margin: 0.5rem 0 0 0;">Datei hier hineinziehen oder per Dateiauswahl wählen.</p>
            {#if uploadForm.file}
              <p style="margin: 0.25rem 0 0 0;"><strong>Ausgewählt:</strong> {uploadForm.file.name}</p>
            {/if}
          </div>
          <div style="flex: 1 1 260px;">
            <label for="upload-notes">Notizen</label>
            <input id="upload-notes" bind:value={uploadForm.notes} />
          </div>
        </div>
        <p style="margin-top: 1rem;">
          <button onclick={uploadPdf}>PDF hochladen</button>
        </p>
      </div>
    {:else if modalTab === "collections" && me.user_group === "admin"}
      <div class="card">
        <h4>Sammlungen für dieses Stück</h4>
        {#if collections.length === 0}
          <p>Noch keine Sammlungen vorhanden.</p>
        {:else}
          <div class="row" style="margin-bottom: 1rem;">
            {#each collections as collection}
              <label style="min-width: 220px;">
                <input type="checkbox" bind:group={selectedSongCollectionIds} value={collection.id} />
                {collection.name}
              </label>
            {/each}
          </div>
          <p><button onclick={saveSongCollections}>Zuordnung speichern</button></p>
        {/if}
      </div>
    {/if}
  </dialog>

  {#if me.user_group === "admin"}
    <section class="card">
      <h2>Sammlungen verwalten</h2>
      <div class="row">
        <input placeholder="Neue Sammlung" bind:value={collectionForm.name} />
        <input placeholder="Notizen" bind:value={collectionForm.notes} />
        <button onclick={createNewCollection}>Sammlung anlegen</button>
      </div>
      {#if collections.length > 0}
        <table style="margin-top: 1rem;">
          <thead>
            <tr>
              <th>Name</th>
              <th>Notizen</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody>
            {#each collections as collection}
              <tr>
                <td><input bind:value={collection.name} /></td>
                <td><input bind:value={collection.notes} /></td>
                <td>
                  <div class="row">
                    <button onclick={() => saveCollection(collection)}>Speichern</button>
                    <button class="warn" onclick={() => removeCollection(collection)}>Löschen</button>
                  </div>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}
{/if}
