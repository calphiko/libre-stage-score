<script>
  import { onMount } from "svelte";
  import { AllCommunityModule, ModuleRegistry, createGrid, themeQuartz } from "ag-grid-community";

  export let rows = [];
  export let columns = [];
  export let onEdit = () => {};
  export let label = "Datentabelle";
  export let noRowsText = "Keine Einträge gefunden";
  export let getRowId = ({ data }) => String(data.id);
  export let fixedColumns = [];
  export let fillHeight = false;

  let gridElement;
  let api;
  let visibleColumns = {};

  const theme = themeQuartz.withParams({
    backgroundColor: "var(--color-surface-900)",
    foregroundColor: "var(--color-surface-100)",
    headerBackgroundColor: "var(--color-surface-800)",
    borderColor: "var(--color-surface-700)",
    accentColor: "var(--color-primary-400)",
    fontFamily: "inherit",
  });

  function syncVisibility() {
    if (!api) return;
    visibleColumns = Object.fromEntries(
      api.getColumnState().map((column) => [column.colId, !column.hide])
    );
  }

  function toggleColumn(id, visible) {
    api.setColumnsVisible([id], visible);
    syncVisibility();
  }

  onMount(() => {
    ModuleRegistry.registerModules([AllCommunityModule]);
    api = createGrid(gridElement, {
      theme,
      rowData: rows,
      columnDefs: columns,
      defaultColDef: { flex: 1, sortable: true, filter: "agTextColumnFilter", resizable: true, minWidth: 75 },
      getRowId,
      readOnlyEdit: true,
      onCellEditRequest: (event) => onEdit(event),
      onColumnVisible: syncVisibility,
      localeText: {
        noRowsToShow: noRowsText,
        filterOoo: "Filtern...",
        contains: "Enthält",
        notContains: "Enthält nicht",
        equals: "Ist gleich",
        notEqual: "Ist nicht gleich",
        startsWith: "Beginnt mit",
        endsWith: "Endet mit",
        blank: "Leer",
        notBlank: "Nicht leer",
        andCondition: "UND",
        orCondition: "ODER",
        clearFilter: "Filter löschen",
      },
    });
    syncVisibility();
    return () => {
      api.destroy();
      api = undefined;
    };
  });

  $: if (api) api.setGridOption("rowData", rows);
  $: if (api) {
    api.setGridOption("columnDefs", columns);
    syncVisibility();
  }
</script>

<div class:fill-height={fillHeight} class="grid-container">
  <div class="grid-controls">
    <details>
      <summary>Spalten auswählen</summary>
      <div class="column-picker">
        {#each columns.filter((column) => !fixedColumns.includes(column.colId)) as column (column.colId)}
          <label>
            <input
              type="checkbox"
              checked={visibleColumns[column.colId] ?? !column.initialHide}
              onchange={(event) => toggleColumn(column.colId, event.currentTarget.checked)}
            />
            {column.headerName}
          </label>
        {/each}
      </div>
    </details>
  </div>
  <div bind:this={gridElement} class="data-grid" role="region" aria-label={label}></div>
</div>

<style>
  .fill-height {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 300px;
  }
  .fill-height .data-grid {
    position: relative;
    flex: 1 1 300px;
    height: auto;
  }
  .fill-height .data-grid :global(.ag-root-wrapper) {
    position: absolute;
    inset: 0;
  }
  .grid-controls {
    margin-bottom: 0.6rem;
  }
  summary {
    cursor: pointer;
  }
  .column-picker {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    padding: 0.6rem 0;
  }
  .data-grid {
    width: 100%;
    height: min(65vh, 650px);
    min-height: 300px;
  }
</style>
