<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import {
    deleteScore,
    createSong,
    deleteSong,
    getCollections,
    getInstruments,
    getScores,
    getScoreDocumentUrl,
    getSongs,
    getUser,
    previewScorePdfUpload,
    commitScorePdfUpload,
    updateScore,
    updateSong,
    updateSongCollections,
  } from "$lib/api.js";
  import DeleteIcon from "$lib/components/icons/DeleteIcon.svelte";
  import EditIcon from "$lib/components/icons/EditIcon.svelte";
  import OpenFileIcon from "$lib/components/icons/OpenFileIcon.svelte";
  import SaveIcon from "$lib/components/icons/SaveIcon.svelte";
  import { showToast } from "$lib/toasts.js";

  let me = null;
  let error = "";
  let ok = "";
  let songs = [];
  let collections = [];
  let instruments = [];
  const EDITOR_MODE_STORAGE_KEY = "editor-mode-view";

  let editorViewMode = false;
  let songFilter = "";
  let activeSong = null;
  let activeSongScores = [];
  let scoreInstrumentSelection = {};
  let editingScoreId = null;
  let scoresModal = null;
  let showSongModal = false;
  let modalTab = "scores";
  let selectedSongCollectionIds = [];
  let uploadForm = {
    default_new_instrument_tuning: "C",
    notes: "",
    file: null,
  };
  let uploadStep = "select";
  let uploadPreview = null;
  let chapterMappings = [];
  let isDragActive = false;
  let songForm = { name: "", tune: "", composer: "", arrangement: "", length: "", notes: "" };
  let editingSongId = null;
  let songFormMode = "create";

  $: if (error) {
    showToast(error, "error");
    error = "";
  }

  $: if (ok) {
    showToast(ok, "ok");
    ok = "";
  }

  function resetSongForm() {
    songForm = { name: "", tune: "", composer: "", arrangement: "", length: "", notes: "" };
    editingSongId = null;
    songFormMode = "create";
  }

  function closeSongModal() {
    showSongModal = false;
    resetSongForm();
  }

  function normalizeCollections(items) {
    return items.map((collection) => ({ ...collection, notes: collection.notes ?? "" }));
  }

  function effectiveSongView() {
    if (me?.user_group === "admin") return "editor";
    if (me?.user_group === "editor") return editorViewMode ? "editor" : "user";
    return "user";
  }

  function syncEditorModeState() {
    if (typeof window === "undefined") return;
    const storedValue = window.localStorage.getItem(EDITOR_MODE_STORAGE_KEY);
    if (me?.user_group === "admin") {
      editorViewMode = true;
      return;
    }
    if (me?.user_group === "editor") {
      editorViewMode = storedValue === "true";
    }
  }

  function persistEditorModeState(nextValue) {
    if (typeof window === "undefined") return;
    const active = Boolean(nextValue);
    window.localStorage.setItem(EDITOR_MODE_STORAGE_KEY, String(active));
  }

  async function loadAll() {
    error = "";
    ok = "";
    try {
      me = await getUser();
      syncEditorModeState();

      const view = effectiveSongView();
      const [loadedSongs, loadedInstruments, loadedCollections] = await Promise.all([
        getSongs(view),
        getInstruments(view),
        getCollections(),
      ]);

      songs = loadedSongs;
      instruments = loadedInstruments;
      collections = normalizeCollections(loadedCollections);
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

  function canManageSongs() {
    return me?.user_group === "admin" || me?.user_group === "editor";
  }

  function canToggleEditorMode() {
    return me?.user_group === "editor";
  }

  async function openScoresModal(song) {
    error = "";
    ok = "";
    try {
      activeSong = song;
      modalTab = "scores";
      selectedSongCollectionIds = (song.collections ?? []).map((collection) => collection.id);
      const scores = await getScores(song.id, effectiveSongView());
      const allowedInstrumentIds = new Set(instruments.map((instrument) => Number(instrument.id)));
      activeSongScores = scores.filter(
        (score) => allowedInstrumentIds.has(Number(score.instrument_id)) && isPdfStoragePath(score.storage_path)
      );
      scoreInstrumentSelection = Object.fromEntries(
        activeSongScores.map((score) => [score.id, String(score.instrument_id)])
      );
      scoresModal.showModal();
    } catch (err) {
      error = err.message;
    }
  }

  function closeScoresModal() {
    if (!canLeaveUploadFlow()) return;
    if (scoresModal?.open) {
      scoresModal.close();
    }
    activeSong = null;
    activeSongScores = [];
    scoreInstrumentSelection = {};
    editingScoreId = null;
    selectedSongCollectionIds = [];
    uploadForm = {
      default_new_instrument_tuning: "C",
      notes: "",
      file: null,
    };
    uploadStep = "select";
    uploadPreview = null;
    chapterMappings = [];
    isDragActive = false;
  }

  function hasUnfinishedUploadFlow() {
    return modalTab === "upload" && (uploadStep === "mapping" || uploadPreview != null || uploadForm.file != null);
  }

  function canLeaveUploadFlow() {
    if (!hasUnfinishedUploadFlow()) return true;
    return confirm("Der Upload-Flow ist noch nicht abgeschlossen. Beim Verlassen geht dein Fortschritt verloren. Trotzdem fortfahren?");
  }

  function switchModalTab(nextTab) {
    if (nextTab === modalTab) return;
    if (modalTab === "upload" && nextTab !== "upload" && !canLeaveUploadFlow()) return;
    modalTab = nextTab;
  }

  async function refreshActiveSongScores() {
    if (!activeSong) return;
    const scores = await getScores(activeSong.id, effectiveSongView());
    const allowedInstrumentIds = new Set(instruments.map((instrument) => Number(instrument.id)));
    activeSongScores = scores.filter(
      (score) => allowedInstrumentIds.has(Number(score.instrument_id)) && isPdfStoragePath(score.storage_path)
    );
    scoreInstrumentSelection = Object.fromEntries(
      activeSongScores.map((score) => [score.id, String(score.instrument_id)])
    );
    if (editingScoreId != null && !activeSongScores.some((score) => score.id === editingScoreId)) {
      editingScoreId = null;
    }
  }

  function canEditScoreAssignments() {
    return canManageSongs();
  }

  async function removeScore(score) {
    error = "";
    ok = "";
    if (!canEditScoreAssignments()) return;
    if (!confirm(`Dieses PDF für "${score.instrument_name}" wirklich löschen?`)) return;
    try {
      await deleteScore(score.id);
      ok = "PDF gelöscht.";
      if (editingScoreId === score.id) {
        editingScoreId = null;
      }
      await refreshActiveSongScores();
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  async function saveScoreInstrumentAssignment(score) {
    error = "";
    ok = "";
    if (!canEditScoreAssignments()) return;
    const selectedInstrumentId = scoreInstrumentSelection[score.id];
    if (!selectedInstrumentId) {
      error = "Bitte eine Stimme auswählen.";
      return;
    }
    try {
      await updateScore(score.id, {
        song_id: score.song_id,
        instrument_id: Number(selectedInstrumentId),
        storage_path: score.storage_path,
        notes: score.notes ?? null,
      });
      ok = "Stimmenzuordnung aktualisiert.";
      editingScoreId = null;
      await refreshActiveSongScores();
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  function beginScoreInstrumentEdit(score) {
    if (!canEditScoreAssignments()) return;
    editingScoreId = score.id;
  }

  function cancelScoreInstrumentEdit() {
    editingScoreId = null;
  }

  async function refreshSongsAndCollections() {
    const view = effectiveSongView();
    const [loadedSongs, loadedCollections] = await Promise.all([getSongs(view), getCollections()]);
    songs = loadedSongs;
    collections = normalizeCollections(loadedCollections);
    if (activeSong) {
      activeSong = songs.find((song) => song.id === activeSong.id) ?? activeSong;
      selectedSongCollectionIds = (activeSong.collections ?? []).map((collection) => collection.id);
    }
  }

  function chapterDisplayRange(chapter) {
    return `${chapter.start_page + 1}-${chapter.end_page}`;
  }

  function setAllChapterImportSelection(include) {
    chapterMappings = chapterMappings.map((mapping) => ({ ...mapping, include }));
  }

  function initializeChapterMappings(preview) {
    chapterMappings = preview.chapters.map((chapter) => {
      const originalChapterTitle = chapter.original_chapter_title ?? chapter.chapter_title ?? `Kapitel ${chapter.chapter_index + 1}`;
      const suggestedInstrumentId = chapter.suggested_instrument_id != null ? String(chapter.suggested_instrument_id) : "";
      const knownInstrumentId = instruments.some((instrument) => String(instrument.id) === suggestedInstrumentId)
        ? suggestedInstrumentId
        : "";

      if (knownInstrumentId) {
        return {
          chapter_index: chapter.chapter_index,
          include: true,
          use_new_instrument: false,
          instrument_id: knownInstrumentId,
          new_instrument_name: originalChapterTitle,
          new_instrument_tuning: chapter.suggested_instrument_tuning ?? uploadForm.default_new_instrument_tuning,
        };
      }
      return {
        chapter_index: chapter.chapter_index,
        include: true,
        use_new_instrument: true,
        instrument_id: instruments.length > 0 ? String(instruments[0].id) : "",
        new_instrument_name: originalChapterTitle,
        new_instrument_tuning: uploadForm.default_new_instrument_tuning,
      };
    });
  }

  async function readPdfForVoiceAssignment() {
    error = "";
    ok = "";
    if (!canManageSongs()) {
      error = "Nur Admins und Editoren dürfen PDFs hochladen.";
      return;
    }
    if (!activeSong) {
      error = "Kein Stück ausgewählt.";
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
      uploadPreview = await previewScorePdfUpload(activeSong.id, uploadForm.file);
      uploadStep = "mapping";
      initializeChapterMappings(uploadPreview);
      ok = "PDF eingelesen. Bitte Stimmenzuordnung prüfen.";
    } catch (err) {
      error = err.message;
    }
  }

  async function uploadPdf() {
    error = "";
    ok = "";
    if (!activeSong || !uploadPreview) {
      error = "Bitte zuerst eine PDF einlesen.";
      return;
    }
    if (chapterMappings.length === 0) {
      error = "Keine Kapitel zur Zuordnung gefunden.";
      return;
    }

    const selectedMappings = chapterMappings.filter((mapping) => mapping.include);
    if (selectedMappings.length === 0) {
      error = "Bitte mindestens eine Stimme zum Import auswählen.";
      return;
    }

    for (const mapping of selectedMappings) {
      if (mapping.use_new_instrument) {
        if (!mapping.new_instrument_name.trim()) {
          error = "Bitte für alle neuen Stimmen einen Namen angeben.";
          return;
        }
      } else if (!mapping.instrument_id) {
        error = "Bitte für alle Kapitel eine vorhandene Stimme auswählen.";
        return;
      }
    }

    try {
      await commitScorePdfUpload({
        song_id: activeSong.id,
        upload_token: uploadPreview.upload_token,
        notes: uploadForm.notes?.trim() ? uploadForm.notes.trim() : null,
        mappings: chapterMappings.map((mapping) => ({
          chapter_index: mapping.chapter_index,
          include: Boolean(mapping.include),
          instrument_id: mapping.include ? (mapping.use_new_instrument ? null : Number(mapping.instrument_id)) : null,
          create_instrument_name: mapping.include && mapping.use_new_instrument ? mapping.new_instrument_name.trim() : null,
          create_instrument_tuning:
            mapping.include && mapping.use_new_instrument ? mapping.new_instrument_tuning.trim() || "C" : null,
        })),
      });

      ok = "PDF wurde hochgeladen.";
      uploadForm = { default_new_instrument_tuning: "C", notes: "", file: null };
      uploadStep = "select";
      uploadPreview = null;
      chapterMappings = [];
      instruments = await getInstruments(effectiveSongView());
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
    if (!canManageSongs() || !activeSong) {
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

  function beginSongCreate() {
    resetSongForm();
    songFormMode = "create";
    showSongModal = true;
  }

  function beginSongEdit(song) {
    editingSongId = song.id;
    songFormMode = "edit";
    songForm = {
      name: song.name ?? "",
      tune: song.tune ?? "",
      composer: song.composer ?? "",
      arrangement: song.arrangement ?? "",
      length: song.length ?? "",
      notes: song.notes ?? "",
    };
    showSongModal = true;
  }

  async function saveSong() {
    error = "";
    ok = "";
    const payload = {
      name: songForm.name.trim(),
      tune: songForm.tune.trim() || null,
      composer: songForm.composer.trim() || null,
      arrangement: songForm.arrangement.trim() || null,
      length: songForm.length ? songForm.length : null,
      notes: songForm.notes.trim() || null,
    };

    if (!payload.name) {
      error = "Bitte einen Titel für das Stück eingeben.";
      return;
    }

    try {
      if (songFormMode === "edit" && editingSongId != null) {
        await updateSong(editingSongId, payload);
        ok = "Stück aktualisiert.";
      } else {
        await createSong(payload);
        ok = "Stück angelegt.";
      }
      showSongModal = false;
      closeSongModal();
      await refreshSongsAndCollections();
      if (activeSong) {
        activeSong = songs.find((song) => song.id === activeSong.id) ?? activeSong;
      }
    } catch (err) {
      error = err.message;
    }
  }

  async function removeSong(song) {
    error = "";
    ok = "";
    const confirmed = confirm(
      `Dieses Stück "${song.name}" wirklich löschen?\n\nAchtung: Dabei werden auch alle zugehörigen PDFs aus dem Storage gelöscht.`
    );
    if (!confirmed) {
      return;
    }
    try {
      await deleteSong(song.id);
      ok = "Stück inklusive zugehöriger PDFs gelöscht.";
      if (activeSong?.id === song.id) {
        closeScoresModal();
      }
      await refreshSongsAndCollections();
    } catch (err) {
      error = err.message;
    }
  }

  onMount(() => {
    loadAll();
    const onSongsChanged = () => {
      loadAll();
    };
    window.addEventListener("songs:changed", onSongsChanged);
    return () => {
      window.removeEventListener("songs:changed", onSongsChanged);
    };
  });
  function selectUploadFile(file) {
    if (!file) return;
    if (!isPdfStoragePath(file.name)) {
      error = "Es sind nur PDF-Dateien erlaubt.";
      return;
    }
    if (uploadStep === "mapping" && uploadPreview && !confirm("Die aktuelle Stimmenzuordnung geht verloren. Neue PDF trotzdem auswählen?")) {
      return;
    }
    uploadForm.file = file;
    uploadStep = "select";
    uploadPreview = null;
    chapterMappings = [];
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
  .app-dialog {
    width: min(1500px, 96vw);
    max-width: 96vw;
    min-height: 70vh;
    max-height: 90vh;
    overflow: auto;
  }

  .mapping-hint {
    display: block;
    margin-top: 0.35rem;
    color: var(--color-surface-600);
    font-size: 0.85rem;
    line-height: 1.4;
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

  .action-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border-radius: var(--radius-base, 0.375rem);
    border: 1px solid transparent;
    text-decoration: none;
    line-height: 1;
    font-size: 1rem;
    cursor: pointer;
  }

  .action-open {
    background: var(--color-primary-100);
    color: var(--color-primary-700);
    border-color: var(--color-primary-300);
  }

  .action-delete {
    background: var(--color-error-100);
    color: var(--color-error-700);
    border-color: var(--color-error-300);
  }

  .inline-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    cursor: pointer;
  }

  .inline-toggle--filter {
    padding: 0.25rem 0.55rem;
    border: 1px solid var(--color-border, rgba(15, 23, 42, 0.15));
    border-radius: 999px;
    background: var(--color-neutral-50, rgba(148, 163, 184, 0.08));
    color: var(--color-text, inherit);
  }

  .inline-toggle--filter input {
    accent-color: var(--color-primary-600, #2563eb);
  }
</style>

{#if me}
  <section class="card">
    <div class="row" style="align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem;">
      <div class="row" style="align-items: center; gap: 0.75rem;">
        <h2 style="margin: 0;">Stücke</h2>
        {#if canToggleEditorMode()}
          <label class="inline-toggle inline-toggle--filter" style="margin: 0;">
            <input
              type="checkbox"
              checked={editorViewMode}
              onchange={(event) => {
                editorViewMode = event.currentTarget.checked;
                persistEditorModeState(editorViewMode);
                loadAll();
              }}
            />
            <span>{editorViewMode ? "Alle Stücke" : "Nur meine Gruppe"}</span>
          </label>
        {/if}
      </div>
      {#if canManageSongs()}
        <button
          aria-label="Neues Stück anlegen"
          title="Neues Stück anlegen"
          onclick={beginSongCreate}
          style="min-width: 2.5rem; padding-inline: 0.75rem;"
        >
          +
        </button>
      {/if}
    </div>
    <p>
      <input placeholder="Filtern (Name, Tonart, Komponist, Arrangement, Notizen)" bind:value={songFilter} />
    </p>

    <div class="table-wrap">
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
            {#if canManageSongs()}
              <th>Aktion</th>
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each filteredSongs as song}
            <tr class="clickable-row" onclick={() => openScoresModal(song)}>
              <td>{song.id}</td>
              <td>{song.name}</td>
              <td>{song.tune ?? "-"}</td>
              <td>{song.composer ?? "-"}</td>
              <td>{song.arrangement ?? "-"}</td>
              <td>{collectionNames(song)}</td>
              <td>{song.length ?? "-"}</td>
              <td>{song.notes ?? "-"}</td>
              {#if canManageSongs()}
                <td>
                  <div class="row">
                    <button class="secondary" onclick={(event) => { event.stopPropagation(); beginSongEdit(song); }} aria-label="Stück bearbeiten" title="Stück bearbeiten"><EditIcon /></button>
                    <button class="warn" onclick={(event) => { event.stopPropagation(); removeSong(song); }} aria-label="Stück löschen" title="Stück löschen"><DeleteIcon /></button>
                  </div>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

  <dialog bind:this={scoresModal} class="app-dialog">
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h3 style="margin: 0;">Stimmen für {activeSong?.name}</h3>
      <button type="button" class="secondary" onclick={closeScoresModal} aria-label="Schließen" title="Schließen">✕</button>
    </div>
    <div class="modal-tabs">
      <button class={modalTab === "scores" ? "tab-active" : "secondary"} onclick={() => switchModalTab("scores")}
        >Verfügbare Stimmen</button
      >
      {#if canManageSongs()}
        <button class={modalTab === "upload" ? "tab-active" : "secondary"} onclick={() => switchModalTab("upload")}
          >PDF hochladen</button
        >
        <button
          class={modalTab === "collections" ? "tab-active" : "secondary"}
          onclick={() => switchModalTab("collections")}
        >
          Sammlungen
        </button>
      {/if}
    </div>

    {#if modalTab === "scores"}
      {#if activeSongScores.length === 0}
        <p>Keine PDF-Stimmen für dieses Stück verfügbar.</p>
      {:else}
        <div class="table-wrap">
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
                  <td>
                    {#if canEditScoreAssignments() && editingScoreId === score.id}
                      <div class="row" style="gap: 0.5rem; flex-wrap: wrap;">
                        <select bind:value={scoreInstrumentSelection[score.id]}>
                          {#each instruments as instrument}
                            <option value={instrument.id}>
                              {instrument.instrument_name} ({instrument.instrument_tuning})
                            </option>
                          {/each}
                        </select>
                        <button class="secondary" onclick={() => saveScoreInstrumentAssignment(score)} aria-label="Zuweisung speichern" title="Zuweisung speichern"><SaveIcon /></button>
                        <button class="secondary" onclick={cancelScoreInstrumentEdit} aria-label="Bearbeiten abbrechen" title="Bearbeiten abbrechen">✕</button>
                      </div>
                    {:else}
                      <div class="row" style="gap: 0.5rem; flex-wrap: wrap;">
                        <span>{score.instrument_name} ({score.instrument_tuning ?? "-"})</span>
                        {#if canEditScoreAssignments()}
                          <button
                            class="secondary"
                            onclick={() => beginScoreInstrumentEdit(score)}
                            aria-label="Stimmenzuordnung bearbeiten"
                            title="Stimmenzuordnung bearbeiten"
                            style="padding: 0.15rem 0.4rem; min-width: 1.8rem; line-height: 1;"
                          >
                            <EditIcon />
                          </button>
                        {/if}
                      </div>
                    {/if}
                  </td>
                  <td>{score.storage_path}</td>
                  <td>
                    <div class="row" style="gap: 0.5rem; flex-wrap: wrap;">
                      <a
                        href={getScoreDocumentUrl(score.id)}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="action-icon action-open"
                        aria-label="PDF öffnen"
                        title="PDF öffnen"
                      >
                        <OpenFileIcon />
                      </a>
                      {#if canEditScoreAssignments()}
                        <button
                          class="action-icon action-delete"
                          onclick={() => removeScore(score)}
                          aria-label="PDF löschen"
                          title="PDF löschen"
                        >
                          <DeleteIcon />
                        </button>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {:else if modalTab === "upload" && canManageSongs()}
      <div class="card">
        <div class="row">
          <div style="flex: 1 1 260px;">
            <label for="upload-default-tuning">Standard-Stimmung für neue Stimmen</label>
            <input id="upload-default-tuning" placeholder="z. B. C oder Bb" bind:value={uploadForm.default_new_instrument_tuning} />
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
            {#if uploadStep === "mapping" && uploadPreview}
              <p style="margin: 0.25rem 0 0 0;">
                <strong>PDF eingelesen:</strong> {uploadPreview.chapters.length} Kapitel erkannt ({uploadPreview.page_count}
                Seiten)
              </p>
            {/if}
          </div>
          <div style="flex: 1 1 260px;">
            <label for="upload-notes">Notizen</label>
            <input id="upload-notes" bind:value={uploadForm.notes} />
          </div>
        </div>
        {#if uploadStep === "select"}
          <p style="margin-top: 1rem;">
            <button onclick={readPdfForVoiceAssignment}>PDF einlesen</button>
          </p>
        {:else if uploadPreview}
          <div class="table-wrap" style="margin-top: 1rem;">
            <table>
              <thead>
                <tr>
                  <th>
                    <label style="font-weight: normal;">
                      <input
                        type="checkbox"
                        checked={chapterMappings.length > 0 && chapterMappings.every((mapping) => mapping.include)}
                        onchange={(event) => setAllChapterImportSelection(event.currentTarget.checked)}
                      />
                      Alle
                    </label>
                  </th>
                  <th>Kapitel</th>
                  <th>Seiten</th>
                  <th>Zuordnung</th>
                </tr>
              </thead>
              <tbody>
                {#each uploadPreview.chapters as chapter, index (chapter.chapter_index)}
                  <tr>
                    <td>
                      <input type="checkbox" bind:checked={chapterMappings[index].include} />
                    </td>
                    <td>{chapter.original_chapter_title ?? chapter.chapter_title ?? `Kapitel ${chapter.chapter_index + 1}`}</td>
                    <td>{chapterDisplayRange(chapter)}</td>
                    <td>
                      {#if chapterMappings[index].include}
                        <div>
                          <label style="font-weight: normal;">
                            <input type="checkbox" bind:checked={chapterMappings[index].use_new_instrument} />
                            Neue Stimme anlegen
                          </label>
                        </div>
                        {#if chapterMappings[index].use_new_instrument}
                          <input
                            style="margin-top: 0.25rem;"
                            placeholder="Neue Stimme"
                            bind:value={chapterMappings[index].new_instrument_name}
                          />
                          <input
                            style="margin-top: 0.25rem;"
                            placeholder="Stimmung"
                            bind:value={chapterMappings[index].new_instrument_tuning}
                          />
                        {:else}
                          <select style="margin-top: 0.25rem;" bind:value={chapterMappings[index].instrument_id}>
                            <option value="">-- bitte wählen --</option>
                            {#each instruments as instrument}
                              <option value={String(instrument.id)}>
                                {instrument.instrument_name} ({instrument.instrument_tuning})
                              </option>
                            {/each}
                          </select>
                        {/if}
                        {#if uploadPreview.chapters[index]?.suggested_instrument_name}
                          <span class="mapping-hint">
                            Vorschlag: {uploadPreview.chapters[index].suggested_instrument_name}
                            {#if uploadPreview.chapters[index].suggested_instrument_tuning}
                              ({uploadPreview.chapters[index].suggested_instrument_tuning})
                            {/if}
                          </span>
                        {/if}
                      {:else}
                        <span>-</span>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          <p style="margin-top: 1rem;">
            <button onclick={uploadPdf}>PDF hochladen</button>
          </p>
        {/if}
      </div>
    {:else if modalTab === "collections" && canManageSongs()}
      <div class="card">
        <h4>Sammlungen für dieses Stück</h4>
        <p>
          Sammlungen anlegen, umbenennen und löschen erfolgt auf der Seite
          <a href="/collections">Sammlungen</a>.
        </p>
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
{/if}

{#if canManageSongs() && showSongModal}
  <dialog open class="app-dialog">
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h3 style="margin: 0;">{songFormMode === "edit" ? "Stück bearbeiten" : "Neues Stück anlegen"}</h3>
      <button type="button" class="secondary" onclick={closeSongModal} aria-label="Schließen" title="Schließen">✕</button>
    </div>
    <div class="card" style="margin-top: 1rem;">
      <div class="row">
        <div style="flex: 1 1 220px;">
          <label for="song-name">Name</label>
          <input id="song-name" bind:value={songForm.name} />
        </div>
        <div style="flex: 1 1 160px;">
          <label for="song-tune">Tonart</label>
          <input id="song-tune" bind:value={songForm.tune} />
        </div>
        <div style="flex: 1 1 220px;">
          <label for="song-composer">Komponist</label>
          <input id="song-composer" bind:value={songForm.composer} />
        </div>
      </div>
      <div class="row" style="margin-top: 1rem;">
        <div style="flex: 1 1 220px;">
          <label for="song-arrangement">Arrangement</label>
          <input id="song-arrangement" bind:value={songForm.arrangement} />
        </div>
        <div style="flex: 1 1 160px;">
          <label for="song-length">Länge</label>
          <input id="song-length" type="time" step="1" bind:value={songForm.length} />
        </div>
        <div style="flex: 1 1 220px;">
          <label for="song-notes">Notizen</label>
          <input id="song-notes" bind:value={songForm.notes} />
        </div>
      </div>
      <p style="margin-top: 1rem;">
        <button onclick={saveSong} aria-label={songFormMode === "edit" ? "Änderungen speichern" : "Stück anlegen"} title={songFormMode === "edit" ? "Änderungen speichern" : "Stück anlegen"}><SaveIcon /> {songFormMode === "edit" ? "Speichern" : "Anlegen"}</button>
        <button class="secondary" onclick={resetSongForm} aria-label="Formular zurücksetzen" title="Formular zurücksetzen">↺ Zurücksetzen</button>
      </p>
    </div>
  </dialog>
{/if}
