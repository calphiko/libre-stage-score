<script>
  import { goto } from "$app/navigation";
  import { tick } from "svelte";
  import { commitNewSongPdfUpload, getInstruments, getSongMetadata, previewNewSongPdfUpload } from "$lib/api.js";
  import { showToast } from "$lib/toasts.js";

  export let canManage = false;
  export let onOpen = () => {};

  let showModal = false;
  let isDragActive = false;
  let instruments = [];
  $: sortedInstruments = [...instruments].sort((a, b) =>
    a.instrument_name.localeCompare(b.instrument_name, "de", { sensitivity: "base" })
  );
  let songMetadata = null;
  let isLoadingInstruments = false;
  let isAnalyzingPdf = false;
  let isSubmitting = false;
  let uploadStep = "select";
  let uploadPreview = null;
  let pdfPreviewUrl = "";
  let isPdfPreviewExpanded = false;
  let chapterPreviewImage = null;
  let chapterPreviewDialog;
  let chapterMappings = [];
  let form = {
    file: null,
    default_new_instrument_tuning: "C",
    score_notes: "",
    name: "",
    tune: "",
    composer: "",
    arrangement: "",
    publisher: "",
    physical_storage_id: "",
    ensemble_id: "",
    kind_of_song_id: "",
    difficulty_level_id: "",
    song_rythm_id: "",
    score_completed: false,
    song_with_solo: false,
  };

  function resetFlow() {
    if (pdfPreviewUrl) {
      URL.revokeObjectURL(pdfPreviewUrl);
      pdfPreviewUrl = "";
    }
    showModal = false;
    isDragActive = false;
    isLoadingInstruments = false;
    isAnalyzingPdf = false;
    isSubmitting = false;
    uploadStep = "select";
    uploadPreview = null;
    isPdfPreviewExpanded = false;
    chapterPreviewImage = null;
    chapterMappings = [];
    form = {
      file: null,
      default_new_instrument_tuning: "C",
      score_notes: "",
      name: "",
      tune: "",
      composer: "",
      arrangement: "",
      publisher: "",
      physical_storage_id: "",
      ensemble_id: "",
      kind_of_song_id: "",
      difficulty_level_id: "",
      song_rythm_id: "",
      score_completed: false,
      song_with_solo: false,
    };
  }

  function isPdfStoragePath(path) {
    return (path ?? "").toString().toLowerCase().endsWith(".pdf");
  }

  async function openModal() {
    if (!canManage) return;
    onOpen();
    showModal = true;
    try {
      isLoadingInstruments = true;
      const [loadedInstruments, loadedSongMetadata] = await Promise.all([
        getInstruments("editor"),
        getSongMetadata(),
      ]);
      instruments = loadedInstruments;
      songMetadata = loadedSongMetadata;
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      isLoadingInstruments = false;
    }
  }

  function closeModal() {
    if (isSubmitting || isAnalyzingPdf) return;
    resetFlow();
  }

  function chapterDisplayRange(chapter) {
    return `${chapter.start_page + 1}-${chapter.end_page}`;
  }

  function togglePdfPreview(event = null) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!pdfPreviewUrl || isAnalyzingPdf) return;
    isPdfPreviewExpanded = !isPdfPreviewExpanded;
  }

  async function openChapterPreview(event, image) {
    event.stopPropagation();
    chapterPreviewImage = image;
    await tick();
    if (chapterPreviewDialog && !chapterPreviewDialog.open) {
      chapterPreviewDialog.showModal();
    }
  }

  function closeChapterPreview(event = null) {
    event?.stopPropagation();
    if (chapterPreviewDialog?.open) {
      chapterPreviewDialog.close();
    }
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
          new_instrument_tuning: chapter.suggested_instrument_tuning ?? form.default_new_instrument_tuning,
        };
      }
      return {
        chapter_index: chapter.chapter_index,
        include: true,
        use_new_instrument: true,
        instrument_id: instruments.length > 0 ? String(instruments[0].id) : "",
        new_instrument_name: originalChapterTitle,
        new_instrument_tuning: form.default_new_instrument_tuning,
      };
    });
  }

  function applySuggestedMetadata(preview) {
    const nextName = (preview?.suggested_song_name ?? "").toString().trim();
    const nextComposer = (preview?.suggested_composer ?? "").toString().trim();
    const nextArrangement = (preview?.suggested_arrangement ?? "").toString().trim();

    form = {
      ...form,
      name: nextName || form.name,
      composer: nextComposer || form.composer,
      arrangement: nextArrangement || form.arrangement,
    };
  }

  function selectUploadFile(file) {
    if (!file || isAnalyzingPdf) return;
    if (!isPdfStoragePath(file.name)) {
      showToast("Es sind nur PDF-Dateien erlaubt.", "error");
      return;
    }
    if (uploadStep === "mapping" && uploadPreview && !confirm("Die aktuelle Zuordnung geht verloren. Neue PDF trotzdem auswählen?")) {
      return;
    }
    if (pdfPreviewUrl) {
      URL.revokeObjectURL(pdfPreviewUrl);
    }
    pdfPreviewUrl = URL.createObjectURL(file);
    form.file = file;
    uploadStep = "select";
    uploadPreview = null;
    chapterMappings = [];
  }

  function handleFileInputChange(event) {
    selectUploadFile(event.currentTarget.files?.[0] ?? null);
  }

  function handleDrop(event) {
    event.preventDefault();
    isDragActive = false;
    selectUploadFile(event.dataTransfer?.files?.[0] ?? null);
  }

  async function analyzePdf() {
    if (!canManage) {
      showToast("Nur Admins und Editoren dürfen Stücke anlegen.", "error");
      return;
    }
    if (!form.file) {
      showToast("Bitte eine PDF-Datei auswählen.", "error");
      return;
    }
    if (!isPdfStoragePath(form.file.name)) {
      showToast("Es sind nur PDF-Dateien erlaubt.", "error");
      return;
    }
    if (isAnalyzingPdf) return;

    isAnalyzingPdf = true;
    try {
      uploadPreview = await previewNewSongPdfUpload(form.file);
      uploadStep = "mapping";
      initializeChapterMappings(uploadPreview);
      applySuggestedMetadata(uploadPreview);
      showToast("PDF analysiert. Bitte Angaben prüfen und importieren.", "ok");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      isAnalyzingPdf = false;
    }
  }

  async function createSongFromPdf() {
    if (!uploadPreview) {
      showToast("Bitte zuerst eine PDF analysieren.", "error");
      return;
    }
    if (!form.name.trim()) {
      showToast("Bitte einen Titel für das Stück eingeben.", "error");
      return;
    }

    const selectedMappings = chapterMappings.filter((mapping) => mapping.include);
    if (selectedMappings.length === 0) {
      showToast("Bitte mindestens eine Stimme zum Import auswählen.", "error");
      return;
    }

    for (const mapping of selectedMappings) {
      if (mapping.use_new_instrument) {
        if (!mapping.new_instrument_name.trim()) {
          showToast("Bitte für alle neuen Stimmen einen Namen angeben.", "error");
          return;
        }
      } else if (!mapping.instrument_id) {
        showToast("Bitte für alle Kapitel eine vorhandene Stimme auswählen.", "error");
        return;
      }
    }

    try {
      isSubmitting = true;
      const result = await commitNewSongPdfUpload({
        upload_token: uploadPreview.upload_token,
        name: form.name.trim(),
        tune: form.tune.trim() || null,
        composer: form.composer.trim() || null,
        arrangement: form.arrangement.trim() || null,
        publisher: form.publisher.trim() || null,
        physical_storage_id: form.physical_storage_id ? Number(form.physical_storage_id) : null,
        ensemble_id: form.ensemble_id ? Number(form.ensemble_id) : null,
        kind_of_song_id: form.kind_of_song_id ? Number(form.kind_of_song_id) : null,
        difficulty_level_id: form.difficulty_level_id ? Number(form.difficulty_level_id) : null,
        song_rythm_id: form.song_rythm_id ? Number(form.song_rythm_id) : null,
        score_completed: Boolean(form.score_completed),
        song_with_solo: Boolean(form.song_with_solo),
        score_notes: form.score_notes.trim() || null,
        mappings: chapterMappings.map((mapping) => ({
          chapter_index: mapping.chapter_index,
          include: Boolean(mapping.include),
          instrument_id: mapping.include ? (mapping.use_new_instrument ? null : Number(mapping.instrument_id)) : null,
          create_instrument_name: mapping.include && mapping.use_new_instrument ? mapping.new_instrument_name.trim() : null,
          create_instrument_tuning:
            mapping.include && mapping.use_new_instrument ? mapping.new_instrument_tuning.trim() || "C" : null,
        })),
      });
      showToast(`Stück "${result.song?.name ?? form.name.trim()}" wurde angelegt.`, "ok");
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("songs:changed"));
      }
      resetFlow();
      goto("/songs");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      isSubmitting = false;
    }
  }
</script>

{#if canManage}
  <button
    class="secondary app-header__quick-action"
    onclick={openModal}
    aria-label="Neues Stück per PDF anlegen"
    title="Neues Stück per PDF anlegen"
  >
    &#9835;+
  </button>
{/if}

{#if showModal}
  <div
    class="modal-backdrop"
    role="button"
    tabindex="0"
    onclick={closeModal}
    onkeydown={(event) => {
      if (event.target !== event.currentTarget) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
      }
    }}
  >
    <div
      class="modal"
      role="dialog"
      tabindex="0"
      aria-modal="true"
      aria-busy={isAnalyzingPdf}
      aria-labelledby="create-song-pdf-title"
      onclick={(event) => event.stopPropagation()}
      onkeydown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          closeModal();
        }
      }}
    >
      {#if isAnalyzingPdf}
        <div class="modal-loading-overlay" aria-live="polite" aria-label="PDF wird analysiert">
          <div class="spinner" aria-hidden="true"></div>
          <span>PDF wird analysiert…</span>
        </div>
      {/if}
      <div class="row" style="justify-content: space-between; align-items: center;">
        <h3 id="create-song-pdf-title" style="margin: 0;">Neues Stück aus PDF</h3>
        <button type="button" class="secondary" onclick={closeModal} aria-label="Schließen" title="Schließen">✕</button>
      </div>

      <div class="card">
        <div class="row">
          <div style="flex: 1 1 240px;">
            <label for="new-song-upload-default-tuning">Standard-Stimmung für neue Stimmen</label>
            <input
              id="new-song-upload-default-tuning"
              placeholder="z. B. C oder Bb"
              bind:value={form.default_new_instrument_tuning}
            />
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
            <label for="new-song-upload-file">PDF-Datei</label>
            <input
              id="new-song-upload-file"
              type="file"
              accept=".pdf,application/pdf"
              onchange={handleFileInputChange}
              disabled={isAnalyzingPdf}
            />
            <p style="margin: 0.5rem 0 0 0;">Datei hier hineinziehen oder per Dateiauswahl wählen.</p>
            {#if form.file}
              <p style="margin: 0.25rem 0 0 0;"><strong>Ausgewählt:</strong> {form.file.name}</p>
            {/if}
            {#if pdfPreviewUrl}
              <div
                class="pdf-preview-toggle inline-pdf-preview"
                role="button"
                tabindex="0"
                onclick={(event) => togglePdfPreview(event)}
                onkeydown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    togglePdfPreview(event);
                  }
                }}
                aria-label="PDF Vorschau vergrößern"
              >
                <embed src={pdfPreviewUrl} type="application/pdf" title="PDF Vorschau" />
              </div>
            {/if}
            {#if uploadStep === "mapping" && uploadPreview}
              <p style="margin: 0.25rem 0 0 0;">
                <strong>PDF eingelesen:</strong> {uploadPreview.chapters.length} Kapitel erkannt ({uploadPreview.page_count}
                Seiten)
              </p>
            {/if}
          </div>
        </div>
        <p style="margin-top: 1rem;">
          <button onclick={analyzePdf} disabled={isLoadingInstruments || isSubmitting || isAnalyzingPdf}>PDF analysieren</button>
        </p>
      </div>

      {#if isPdfPreviewExpanded && pdfPreviewUrl}
        <div
          class="pdf-preview-overlay"
          role="dialog"
          aria-modal="true"
          tabindex="0"
          aria-label="Große PDF Vorschau"
          onclick={(event) => {
            if (event.target === event.currentTarget) togglePdfPreview(event);
          }}
          onkeydown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              togglePdfPreview(event);
            }
          }}
        >
          <div class="pdf-preview-overlay__content">
            <div class="pdf-preview-overlay__header">
              <strong>PDF Vorschau</strong>
              <button type="button" class="secondary" onclick={(event) => togglePdfPreview(event)} aria-label="Schließen" title="Schließen">✕</button>
            </div>
            <embed src={pdfPreviewUrl} type="application/pdf" title="Große PDF Vorschau" />
          </div>
        </div>
      {/if}

      <dialog
        bind:this={chapterPreviewDialog}
        class="chapter-preview-dialog"
        aria-label="Vergrößerte Stimmvorschau"
        onclick={(event) => {
          if (event.target === event.currentTarget) closeChapterPreview(event);
        }}
        onclose={() => (chapterPreviewImage = null)}
      >
        <div class="chapter-preview-dialog__content">
          <button type="button" class="secondary" onclick={closeChapterPreview} aria-label="Schließen">✕</button>
          {#if chapterPreviewImage}
            <img src={chapterPreviewImage} alt="Vergrößerte Vorschau des oberen Seitenbereichs" />
          {/if}
        </div>
      </dialog>

      {#if uploadStep === "mapping" && uploadPreview}
        <div class="modal-subgrid">
          <div class="card">
            <div class="metadata-header">
              <h4 style="margin: 0;">Erkannte Stückdaten</h4>
            </div>
            <div class="metadata-grid">
              <div class="metadata-fields">
                <label>
                  <span>Titel</span>
                  <input bind:value={form.name} placeholder="Titel" />
                </label>
                <label>
                  <span>Tonart</span>
                  <input bind:value={form.tune} placeholder="z. B. C, Bb, Eb" />
                </label>
                <label>
                  <span>Komponist</span>
                  <input bind:value={form.composer} placeholder="Komponist" />
                </label>
                <label>
                  <span>Arrangeur</span>
                  <input bind:value={form.arrangement} placeholder="Arrangeur" />
                </label>
                <label>
                  <span>Verlag</span>
                  <input bind:value={form.publisher} placeholder="Verlag" />
                </label>
                <label>
                  <span>Besetzung</span>
                  <select bind:value={form.ensemble_id}>
                    <option value="">Keine Auswahl</option>
                    {#each songMetadata?.ensembles ?? [] as ensemble}
                      <option value={String(ensemble.id)}>{ensemble.name}</option>
                    {/each}
                  </select>
                </label>
                <label>
                  <span>Art des Stücks</span>
                  <select bind:value={form.kind_of_song_id}>
                    <option value="">Keine Auswahl</option>
                    {#each songMetadata?.kinds ?? [] as kind}
                      <option value={String(kind.id)}>{kind.name}</option>
                    {/each}
                  </select>
                </label>
                <label>
                  <span>Schwierigkeitsgrad</span>
                  <select bind:value={form.difficulty_level_id}>
                    <option value="">Keine Auswahl</option>
                    {#each songMetadata?.difficulty_levels ?? [] as level}
                      <option value={String(level.id)}>{level.name}</option>
                    {/each}
                  </select>
                </label>
                <label>
                  <span>Rhythmus</span>
                  <select bind:value={form.song_rythm_id}>
                    <option value="">Keine Auswahl</option>
                    {#each songMetadata?.rhythms ?? [] as rhythm}
                      <option value={String(rhythm.id)}>{rhythm.name}</option>
                    {/each}
                  </select>
                </label>
                <label>
                  <span>Physischer Aufbewahrungsort</span>
                  <select bind:value={form.physical_storage_id}>
                    <option value="">Keine Auswahl</option>
                    {#each songMetadata?.physical_storages ?? [] as storage}
                      <option value={String(storage.id)}>{storage.storage_path || storage.notes || `#${storage.id}`}</option>
                    {/each}
                  </select>
                </label>
                <label>
                  <span><input type="checkbox" bind:checked={form.score_completed} /> Notenmaterial vollständig</span>
                </label>
                <label>
                  <span><input type="checkbox" bind:checked={form.song_with_solo} /> Stück enthält ein Solo</span>
                </label>
                <label>
                  <span>Notizen für importierte Stimmen</span>
                  <input bind:value={form.score_notes} placeholder="z. B. nur bestimmte Seiten/Anmerkungen" />
                </label>
              </div>
            </div>
          </div>

          <div class="card">
            <h4 style="margin-top: 0;">Stimmenzuordnung</h4>
            <div class="table-wrap">
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
                    <th>Vorschau</th>
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
                        {#if chapter.top_preview_data_url}
                          <button
                            type="button"
                            class="chapter-preview-thumb"
                            onclick={(event) => openChapterPreview(event, chapter.top_preview_data_url)}
                            aria-label="Vorschau vergrößern"
                            title="Klicken, um die Vorschau zu vergrößern"
                          >
                            <img
                              src={chapter.top_preview_data_url}
                              alt={`Oberer Seitenbereich: ${chapter.original_chapter_title ?? chapter.chapter_title ?? `Kapitel ${chapter.chapter_index + 1}`}`}
                            />
                          </button>
                        {/if}
                      </td>
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
                              {#each sortedInstruments as instrument}
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
          </div>
        </div>
        <div class="modal-actions">
          <button onclick={createSongFromPdf} disabled={isSubmitting}>Stück anlegen</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .app-header__quick-action {
    min-width: 2.2rem;
    padding: 0.15rem 0.45rem;
    line-height: 1;
    font-size: 0.9rem;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: oklch(0 0 0 / 0.72);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 1rem;
  }

  .modal {
    position: relative;
    width: min(1200px, 100%);
    max-height: 90vh;
    overflow: auto;
    background: var(--color-surface-900);
    border: 1px solid var(--color-surface-700);
    border-radius: var(--radius-container);
    box-shadow: 0 24px 70px oklch(0 0 0 / 0.4);
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .modal-loading-overlay {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 0.75rem;
    background: color-mix(in oklab, var(--color-surface-900) 92%, transparent);
    backdrop-filter: blur(2px);
    font-weight: 600;
    color: var(--color-surface-100);
  }

  .spinner {
    width: 2.2rem;
    height: 2.2rem;
    border-radius: 50%;
    border: 3px solid var(--color-surface-700);
    border-top-color: var(--color-primary-400);
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .modal-subgrid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .metadata-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 0.75rem;
  }

  .pdf-preview-toggle {
    padding: 0;
    border: 1px solid var(--color-surface-700);
    border-radius: 8px;
    overflow: hidden;
    background: transparent;
    cursor: pointer;
  }

  .pdf-preview-toggle embed {
    display: block;
    width: 100%;
    height: 100%;
    min-height: 100%;
    pointer-events: none;
  }

  .pdf-preview-overlay {
    position: fixed;
    inset: 0;
    background: oklch(0 0 0 / 0.78);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1100;
    padding: 1rem;
  }

  .pdf-preview-overlay__content {
    width: min(1100px, 100%);
    height: min(90vh, 900px);
    background: var(--color-surface-900);
    border: 1px solid var(--color-surface-700);
    border-radius: var(--radius-container);
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    box-shadow: 0 24px 70px oklch(0 0 0 / 0.4);
  }

  .pdf-preview-overlay__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .pdf-preview-overlay__content embed {
    flex: 1;
    width: 100%;
    min-height: 0;
    border: 1px solid var(--color-surface-700);
    border-radius: 8px;
    background: var(--color-surface-900);
    pointer-events: none;
  }

  .chapter-preview-thumb {
    display: block;
    width: 260px;
    max-width: 30vw;
    padding: 0;
    border: 1px solid var(--color-surface-700);
    border-radius: 4px;
    overflow: hidden;
    background: var(--color-surface-900);
    cursor: zoom-in;
  }

  .chapter-preview-thumb img {
    display: block;
    width: 100%;
    height: auto;
  }

  .chapter-preview-dialog {
    max-width: 98vw;
    max-height: 98vh;
    padding: 0;
    overflow: visible;
    border: 0;
    background: transparent;
  }

  .chapter-preview-dialog::backdrop {
    background: oklch(0 0 0 / 0.82);
  }

  .chapter-preview-dialog__content {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.5rem;
    max-width: calc(98vw - 1.5rem);
    max-height: calc(98vh - 1.5rem);
    padding: 0.75rem;
    overflow: auto;
    border-radius: 8px;
    background: var(--color-surface-900);
    border: 1px solid var(--color-surface-700);
  }

  .chapter-preview-dialog__content img {
    display: block;
    width: min(96vw, 1500px);
    height: auto;
    max-width: 100%;
    max-height: calc(94vh - 3rem);
    object-fit: contain;
  }

  .metadata-grid {
    display: flex;
    gap: 1rem;
  }

  .metadata-fields {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .pdf-preview-box {
    width: 210px;
    min-width: 210px;
    height: 160px;
    border: 1px solid var(--color-surface-700);
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-surface-900);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .pdf-preview-box embed {
    width: 100%;
    height: 100%;
    display: block;
    border: none;
    background: white;
    pointer-events: none;
  }

  .inline-pdf-preview {
    width: min(220px, 100%);
    height: 160px;
    margin-top: 0.75rem;
    border: 1px solid var(--color-surface-700);
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-surface-900);
  }

  .inline-pdf-preview embed {
    width: 100%;
    height: 100%;
    display: block;
    border: none;
    background: white;
    pointer-events: none;
  }

  .drop-zone {
    border: 2px dashed var(--color-surface-700);
    border-radius: var(--radius-base, 0.375rem);
    padding: 1rem;
    background: var(--color-surface-900);
    transition: border-color 0.15s ease-in-out, background 0.15s ease-in-out;
  }

  .drop-zone.active {
    border-color: var(--color-primary-400);
    background: color-mix(in oklab, var(--color-primary-500) 14%, var(--color-surface-900));
  }

  .mapping-hint {
    display: block;
    margin-top: 0.35rem;
    color: var(--color-surface-300);
    font-size: 0.85rem;
    line-height: 1.4;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
