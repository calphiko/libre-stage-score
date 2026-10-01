<script>
  import { goto } from "$app/navigation";
  import { commitNewSongPdfUpload, getInstruments, previewNewSongPdfUpload } from "$lib/api.js";
  import { showToast } from "$lib/toasts.js";

  export let canManage = false;
  export let onOpen = () => {};

  let showModal = false;
  let isDragActive = false;
  let instruments = [];
  let isLoadingInstruments = false;
  let isAnalyzingPdf = false;
  let isSubmitting = false;
  let uploadStep = "select";
  let uploadPreview = null;
  let pdfPreviewUrl = "";
  let isPdfPreviewExpanded = false;
  let chapterMappings = [];
  let form = {
    file: null,
    default_new_instrument_tuning: "C",
    score_notes: "",
    name: "",
    tune: "",
    composer: "",
    arrangement: "",
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
    chapterMappings = [];
    form = {
      file: null,
      default_new_instrument_tuning: "C",
      score_notes: "",
      name: "",
      tune: "",
      composer: "",
      arrangement: "",
    };
  }

  function isPdfStoragePath(path) {
    return (path ?? "").toString().toLowerCase().endsWith(".pdf");
  }

  async function openModal() {
    if (!canManage) return;
    onOpen();
    showModal = true;
    if (instruments.length > 0) return;
    try {
      isLoadingInstruments = true;
      instruments = await getInstruments("editor");
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
      closeModal();
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
    background: rgba(15, 23, 42, 0.5);
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
    background: white;
    border-radius: 12px;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.2);
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
    background: rgba(255, 255, 255, 0.8);
    backdrop-filter: blur(2px);
    font-weight: 600;
    color: var(--color-text-strong, #111827);
  }

  .spinner {
    width: 2.2rem;
    height: 2.2rem;
    border-radius: 50%;
    border: 3px solid rgba(15, 23, 42, 0.15);
    border-top-color: var(--color-primary, #2563eb);
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
    border: 1px solid var(--color-surface-300);
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
    background: rgba(15, 23, 42, 0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1100;
    padding: 1rem;
  }

  .pdf-preview-overlay__content {
    width: min(1100px, 100%);
    height: min(90vh, 900px);
    background: white;
    border-radius: 12px;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    box-shadow: 0 20px 45px rgba(15, 23, 42, 0.2);
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
    border: 1px solid var(--color-surface-300);
    border-radius: 8px;
    background: var(--color-surface-50);
    pointer-events: none;
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
    border: 1px solid var(--color-surface-300);
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-surface-50);
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
    border: 1px solid var(--color-surface-300);
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-surface-50);
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

  .mapping-hint {
    display: block;
    margin-top: 0.35rem;
    color: var(--color-surface-600);
    font-size: 0.85rem;
    line-height: 1.4;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
