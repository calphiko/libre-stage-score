import test from "node:test";
import assert from "node:assert/strict";
import { createSongColumns, matchesSongFilter } from "../src/lib/songGrid.js";

const metadata = {
  ensembles: [{ id: 7, name: "Blasorchester" }],
  physical_storages: [{ id: 2, storage_path: "Regal" }],
  kinds: [], difficulty_levels: [], rhythms: [],
};
const columns = (editable) => createSongColumns(metadata, [], editable, {});

test("default and optional columns cover every song field", () => {
  const defs = columns(true);
  assert.deepEqual(defs.filter((column) => !column.initialHide).map((column) => column.colId), [
    "actions", "name", "composer", "arrangement", "collections", "my_groups",
  ]);
  assert.deepEqual(defs.filter((column) => column.initialHide).map((column) => column.colId), [
    "id", "tune", "length", "notes", "publisher", "physical_storage_id", "ensemble_id", "kind_of_song_id",
    "difficulty_level_id", "song_rythm_id", "score_completed", "song_with_solo",
  ]);
  assert.equal(defs[0].pinned, "left");
  assert.equal(defs.find((column) => column.colId === "name").flex, 2);
  assert.equal(defs.find((column) => column.colId === "composer").flex, 2);
  assert.equal(defs.find((column) => column.colId === "arrangement").flex, 2);
  assert.equal(defs.find((column) => column.colId === "actions").flex, 0);
});

test("only managers can edit fields; derived values and id remain read-only", () => {
  const user = columns(false);
  assert.equal(user.some((column) => column.editable), false);
  const editor = columns(true);
  assert.equal(editor.find((column) => column.colId === "name").editable, true);
  assert.equal(editor.find((column) => column.colId === "collections").editable, true);
  assert.equal(editor.find((column) => column.colId === "id").editable, false);
  assert.notEqual(editor.find((column) => column.colId === "my_groups").editable, true);
});

test("metadata and group columns display and filter by names", () => {
  const defs = columns(true);
  const ensemble = defs.find((column) => column.colId === "ensemble_id");
  assert.equal(ensemble.valueFormatter({ value: 7 }), "Blasorchester");
  assert.equal(ensemble.filterValueGetter({ data: { ensemble_id: 7 } }), "Blasorchester");
  assert.deepEqual(ensemble.cellEditorParams.values, ["", 7]);
  const group = defs.find((column) => column.colId === "my_groups");
  assert.equal(group.filterValueGetter({ data: { my_groups: [{ id: 1, name: "Flöten" }] } }), "Flöten");
});

test("search and multiple group memberships combine without widening access", () => {
  const song = {
    name: "Stück", tune: "C", composer: "Bach", notes: null,
    collections: [{ name: "Sommer" }], my_groups: [{ id: 1, name: "Flöten" }],
  };
  assert.ok(matchesSongFilter(song, " sommer ", []));
  assert.ok(matchesSongFilter(song, "BACH", [1, 2]));
  assert.ok(!matchesSongFilter(song, "Bach", [2]));
  assert.ok(!matchesSongFilter(song, "Mozart", [1]));
  assert.ok(!matchesSongFilter({ ...song, my_groups: [] }, "", [1]));
});
