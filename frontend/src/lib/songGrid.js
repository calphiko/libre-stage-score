const metadataFields = {
  physical_storage_id: ["Lagerort", "physical_storages", "storage_path"],
  ensemble_id: ["Ensemble", "ensembles", "name"],
  kind_of_song_id: ["Stückart", "kinds", "name"],
  difficulty_level_id: ["Schwierigkeitsgrad", "difficulty_levels", "name"],
  song_rythm_id: ["Rhythmus", "rhythms", "name"],
};

export function matchesSongFilter(song, search, selectedGroupIds) {
  const normalize = (value) => (value ?? "").toString().toLowerCase();
  const query = normalize(search).trim();
  const matchesSearch = !query || [
    song.name, song.tune, song.composer, song.arrangement, song.notes,
    song.collections?.map((collection) => collection.name).join(" "),
  ].some((value) => normalize(value).includes(query));
  return matchesSearch && (
    selectedGroupIds.length === 0 || song.my_groups?.some((group) => selectedGroupIds.includes(group.id))
  );
}

class CollectionsEditor {
  init(params) {
    this.params = params;
    this.element = document.createElement("div");
    this.element.className = "song-collections-editor";
    const selected = new Set((params.value ?? []).map((item) => item.id));
    for (const collection of params.collections) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(collection.id);
      checkbox.checked = selected.has(collection.id);
      label.append(checkbox, document.createTextNode(collection.name));
      this.element.append(label);
    }
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Übernehmen";
    save.addEventListener("click", () => params.stopEditing());
    this.element.append(save);
  }
  getGui() { return this.element; }
  isPopup() { return true; }
  getValue() {
    const ids = new Set([...this.element.querySelectorAll("input:checked")].map((input) => Number(input.value)));
    return this.params.collections.filter((collection) => ids.has(collection.id));
  }
}

function iconButton(label, paths, callback, warn = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = warn ? "warn" : "secondary";
  button.title = label;
  button.setAttribute("aria-label", label);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [name, value] of Object.entries({
    width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  })) svg.setAttribute(name, value);
  for (const d of paths) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  button.append(svg);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    callback();
  });
  return button;
}

export function createSongColumns(metadata, collections, editable, actions) {
  const text = (field, headerName, initialHide = false) => ({
    colId: field, field, headerName, initialHide, editable,
    valueFormatter: ({ value }) => value ?? "-",
  });
  const fields = [
    { ...text("id", "ID"), editable: false, filter: "agNumberColumnFilter", width: 100 },
    text("name", "Name"),
    text("tune", "Tonart"),
    text("composer", "Komponist"),
    text("arrangement", "Arrangement"),
    {
      colId: "collections", field: "collections", headerName: "Sammlungen", editable,
      valueFormatter: ({ value }) => value?.map((item) => item.name).join(", ") || "-",
      filterValueGetter: ({ data }) => data.collections?.map((item) => item.name).join(", ") ?? "",
      cellEditor: CollectionsEditor,
      cellEditorParams: { collections },
    },
    text("length", "Länge"),
    text("notes", "Notizen"),
    {
      colId: "my_groups", field: "my_groups", headerName: "Meine Gruppen",
      valueFormatter: ({ value }) => value?.map((group) => group.name).join(", ") || "-",
      filterValueGetter: ({ data }) => data.my_groups?.map((group) => group.name).join(", ") ?? "",
    },
    text("publisher", "Verlag", true),
  ];
  for (const [field, [headerName, list, name]] of Object.entries(metadataFields)) {
    const options = metadata[list] ?? [];
    const display = (value) => options.find((option) => String(option.id) === String(value))?.[name] ?? "";
    fields.push({
      colId: field, field, headerName, initialHide: true, editable,
      valueFormatter: ({ value }) => display(value) || "-",
      filterValueGetter: ({ data }) => display(data[field]),
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: ["", ...options.map((option) => option.id)] },
      valueParser: ({ newValue }) => newValue === "" || newValue == null ? null : Number(newValue),
    });
  }
  for (const [field, headerName] of [["score_completed", "Partitur vollständig"], ["song_with_solo", "Mit Solo"]]) {
    fields.push({
      colId: field, field, headerName, initialHide: true, editable, cellDataType: false,
      valueFormatter: ({ value }) => value ? "Ja" : "Nein",
      filterValueGetter: ({ data }) => data[field] ? "Ja" : "Nein",
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: [true, false] },
    });
  }
  fields.push({
    colId: "actions", headerName: "Aktion", filter: false, sortable: false, editable: false,
    width: editable ? 180 : 110, pinned: "right",
    suppressKeyboardEvent: ({ event }) => {
      if (event.target instanceof HTMLButtonElement) return true;
      if (event.key !== "Tab") return false;
      const buttons = event.target.querySelectorAll("button");
      const button = event.shiftKey ? buttons[buttons.length - 1] : buttons[0];
      if (!button) return false;
      event.preventDefault();
      button.focus();
      return true;
    },
    cellRenderer: ({ data }) => {
      const wrapper = document.createElement("div");
      wrapper.className = "song-grid-actions";
      wrapper.append(iconButton("Stimmen öffnen", ["M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M14 3v6h6", "M10 15h7", "M10 18h7"], () => actions.open(data)));
      if (editable) {
        wrapper.append(iconButton("Stück bearbeiten", ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"], () => actions.edit(data)));
        wrapper.append(iconButton("Stück löschen", ["M3 6h18", "M8 6V4h8v2", "M6 6l1 14h10l1-14", "M10 10v6", "M14 10v6"], () => actions.remove(data), true));
      }
      return wrapper;
    },
  });
  return fields;
}
