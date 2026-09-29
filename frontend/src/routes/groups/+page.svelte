<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import {
    createGroup,
    deleteGroup,
    deleteInstrument,
    getAssignableUsers,
    getGroupInstruments,
    getGroups,
    getInstruments,
    getUser,
    getUserGroups,
    updateGroup,
    updateGroupInstruments,
    updateUserGroups,
  } from "$lib/api.js";

  let me = null;
  let error = "";
  let ok = "";

  let groups = [];
  let instruments = [];
  let users = [];
  let userGroupAssignments = {};
  let groupInstrumentAssignments = {};
  let userFilter = "";
  let instrumentFilter = "";

  let createGroupForm = { name: "", notes: "" };
  let showCreateGroupModal = false;

  function canManageGroups() {
    return me?.user_group === "admin" || me?.user_group === "editor";
  }

  function resetCreateGroupModal() {
    showCreateGroupModal = false;
    createGroupForm = { name: "", notes: "" };
  }

  function openCreateGroupModal() {
    createGroupForm = { name: "", notes: "" };
    showCreateGroupModal = true;
    error = "";
    ok = "";
  }

  function normalizeGroups(items) {
    return items.map((group) => ({ ...group, notes: group.notes ?? "" }));
  }

  async function loadGroupInstrumentAssignments() {
    const assignments = {};
    for (const group of groups) {
      const groupInstruments = await getGroupInstruments(Number(group.id));
      assignments[Number(group.id)] = groupInstruments.map((item) => Number(item.instrument_id));
    }
    groupInstrumentAssignments = assignments;
  }

  async function loadUserGroupAssignments() {
    const assignments = {};
    for (const user of users) {
      const userGroups = await getUserGroups(Number(user.id));
      assignments[Number(user.id)] = userGroups.map((group) => Number(group.id));
    }
    userGroupAssignments = assignments;
  }

  async function loadAll() {
    error = "";
    ok = "";
    try {
      me = await getUser();
      if (!canManageGroups()) {
        goto("/songs");
        return;
      }

      const [loadedGroups, loadedInstruments, loadedUsers] = await Promise.all([
        getGroups(),
        getInstruments(),
        getAssignableUsers(),
      ]);

      groups = normalizeGroups(loadedGroups);
      instruments = loadedInstruments;
      users = loadedUsers;

      await Promise.all([loadGroupInstrumentAssignments(), loadUserGroupAssignments()]);
    } catch (err) {
      error = err.message;
      goto("/");
    }
  }

  async function reloadGroups() {
    const loadedGroups = await getGroups();
    groups = normalizeGroups(loadedGroups);
    if (groups.length === 0) {
      groupInstrumentAssignments = {};
      userGroupAssignments = {};
      return;
    }
    await Promise.all([loadGroupInstrumentAssignments(), loadUserGroupAssignments()]);
  }

  async function createNewGroup() {
    error = "";
    ok = "";
    if (!createGroupForm.name.trim()) {
      error = "Bitte einen Gruppennamen eingeben.";
      return;
    }
    try {
      await createGroup({
        name: createGroupForm.name.trim(),
        notes: createGroupForm.notes.trim() || null,
      });
      resetCreateGroupModal();
      await reloadGroups();
      ok = "Gruppe angelegt.";
    } catch (err) {
      error = err.message;
    }
  }

  async function saveGroup(group) {
    error = "";
    ok = "";
    if (!group.name?.trim()) {
      error = "Bitte einen Gruppennamen eingeben.";
      return;
    }
    try {
      await updateGroup(group.id, {
        name: group.name.trim(),
        notes: group.notes?.trim() || null,
      });
      ok = "Gruppe aktualisiert.";
      await reloadGroups();
    } catch (err) {
      error = err.message;
    }
  }

  async function removeGroup(group) {
    error = "";
    ok = "";
    if (!confirm(`Gruppe "${group.name}" wirklich löschen?`)) return;
    try {
      await deleteGroup(group.id);
      ok = "Gruppe gelöscht.";
      await reloadGroups();
    } catch (err) {
      error = err.message;
    }
  }

  async function toggleGroupInstrumentAssignment(instrumentId, groupId, checked) {
    error = "";
    ok = "";
    const key = Number(groupId);
    const current = new Set(groupInstrumentAssignments[key] ?? []);
    if (checked) {
      current.add(Number(instrumentId));
    } else {
      current.delete(Number(instrumentId));
    }

    const nextInstrumentIds = Array.from(current).sort((a, b) => a - b);
    groupInstrumentAssignments = { ...groupInstrumentAssignments, [key]: nextInstrumentIds };

    try {
      await updateGroupInstruments(key, nextInstrumentIds);
      ok = "Gruppen-Stimmen gespeichert.";
    } catch (err) {
      error = err.message;
      await loadGroupInstrumentAssignments();
    }
  }

  async function toggleUserGroupAssignment(userId, groupId, checked) {
    error = "";
    ok = "";
    const key = Number(userId);
    const groupKey = Number(groupId);
    const current = new Set(userGroupAssignments[key] ?? []);
    if (checked) {
      current.add(groupKey);
    } else {
      current.delete(groupKey);
    }

    const nextGroupIds = Array.from(current).sort((a, b) => a - b);
    userGroupAssignments = { ...userGroupAssignments, [key]: nextGroupIds };

    try {
      await updateUserGroups(key, nextGroupIds);
      ok = "Zuordnung gespeichert.";
    } catch (err) {
      error = err.message;
      await loadUserGroupAssignments();
    }
  }

  onMount(loadAll);
</script>

<style>
  .table-wrap {
    max-height: 420px;
    overflow: auto;
  }

  .table-wrap th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: #f8fafc;
    white-space: nowrap;
  }

  .filter-header {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.4rem;
    min-width: 180px;
  }

  .filter-header input {
    width: 100%;
    min-width: 120px;
    font-size: 0.8rem;
    padding: 0.35rem 0.45rem;
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
    width: min(520px, 100%);
    background: white;
    border-radius: 12px;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.2);
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .field-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
  }

  .modal label {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    font-weight: 600;
  }

  .modal label span {
    font-size: 0.85rem;
    color: #334155;
  }

  .modal input {
    width: 100%;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 0.25rem;
  }
</style>

{#if error}
  <p class="error">{error}</p>
{/if}
{#if ok}
  <p class="ok">{ok}</p>
{/if}

{#if me && canManageGroups()}
  {#if showCreateGroupModal}
    <div
      class="modal-backdrop"
      role="button"
      tabindex="0"
      onclick={resetCreateGroupModal}
      onkeydown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Escape") {
          event.preventDefault();
          resetCreateGroupModal();
        }
      }}
    >
      <div
        class="modal"
        role="dialog"
        tabindex="0"
        aria-modal="true"
        aria-labelledby="create-group-modal-title"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            resetCreateGroupModal();
          }
        }}
      >
        <h3 id="create-group-modal-title">Gruppe anlegen</h3>
        <div class="field-row">
          <label>
            <span>Gruppenname</span>
            <input bind:value={createGroupForm.name} placeholder="z. B. Bläser" />
          </label>
          <label>
            <span>Notizen</span>
            <input bind:value={createGroupForm.notes} placeholder="optional" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="secondary" onclick={resetCreateGroupModal}>Abbrechen</button>
          <button class="primary" onclick={createNewGroup}>Anlegen</button>
        </div>
      </div>
    </div>
  {/if}

  <section class="card">
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2 style="margin: 0;">Gruppen verwalten</h2>
      <button class="primary" aria-label="Gruppe anlegen" title="Gruppe anlegen" onclick={openCreateGroupModal}>＋</button>
    </div>
    {#if groups.length === 0}
      <p>Noch keine Gruppen vorhanden.</p>
    {:else}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Notizen</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody>
            {#each groups as group}
              <tr>
                <td><input bind:value={group.name} /></td>
                <td><input bind:value={group.notes} /></td>
                <td>
                  <div class="row">
                    <button aria-label="Gruppe speichern" title="Speichern" onclick={() => saveGroup(group)}>💾</button>
                    <button
                      class="warn"
                      aria-label="Gruppe löschen"
                      title="Löschen"
                      onclick={() => removeGroup(group)}
                    >
                      🗑
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

  <section class="card">
    <h2>Gruppen-Stimmen zuordnen</h2>
    {#if groups.length === 0 && instruments.length === 0}
      <p>Bitte zuerst eine Gruppe und ein Instrument anlegen.</p>
    {:else if groups.length === 0}
      <p>Bitte zuerst eine Gruppe anlegen.</p>
    {:else if instruments.length === 0}
      <p>Keine Instrumente vorhanden.</p>
    {:else}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <div class="filter-header">
                  <input bind:value={instrumentFilter} placeholder="Instrument" />
                </div>
              </th>
              {#each groups as group}
                <th>{group.name}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each instruments.filter((instrument) => {
              const haystack = `${instrument.instrument_name} ${instrument.instrument_tuning}`.toLowerCase();
              return haystack.includes(instrumentFilter.trim().toLowerCase());
            }) as instrument}
              <tr>
                <td>{instrument.instrument_name} ({instrument.instrument_tuning})</td>
                {#each groups as group}
                  <td>
                    <input
                      type="checkbox"
                      checked={groupInstrumentAssignments[Number(group.id)]?.includes(Number(instrument.id)) ?? false}
                      onchange={(event) => toggleGroupInstrumentAssignment(instrument.id, group.id, event.currentTarget.checked)}
                    />
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section class="card">
    <h2>Benutzer-Gruppen zuordnen</h2>
    {#if users.length === 0 && groups.length === 0}
      <p>Bitte zuerst Gruppen und Benutzer anlegen.</p>
    {:else if users.length === 0}
      <p>Keine zuweisbaren Benutzer gefunden.</p>
    {:else if groups.length === 0}
      <p>Bitte zuerst Gruppen anlegen.</p>
    {:else}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <div class="filter-header">
                  <input bind:value={userFilter} placeholder="Benutzer" />
                </div>
              </th>
              {#each groups as group}
                <th>{group.name}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each users.filter((user) => {
              const haystack = `${user.clear_name} ${user.user_name}`.toLowerCase();
              return haystack.includes(userFilter.trim().toLowerCase());
            }) as user}
              <tr>
                <td>{user.clear_name} ({user.user_name})</td>
                {#each groups as group}
                  <td>
                    <input
                      type="checkbox"
                      checked={userGroupAssignments[Number(user.id)]?.includes(Number(group.id)) ?? false}
                      onchange={(event) => toggleUserGroupAssignment(user.id, group.id, event.currentTarget.checked)}
                    />
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>
{/if}
