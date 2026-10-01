<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import {
    adminActivateUser,
    adminCreateUser,
    adminDeactivateUser,
    adminGetAllUsers,
    adminUpdateUser,
    changePasswordByUser,
    getUser,
    updateUser,
    triggerSendPwResetToken,
  } from "$lib/api.js";
  import { showToast } from "$lib/toasts.js";

  let me = null;
  let error = "";
  let ok = "";
  let allUsers = [];

  $: if (error) {
    showToast(error, "error");
    error = "";
  }

  $: if (ok) {
    showToast(ok, "ok");
    ok = "";
  }

  let selfForm = { clear_name: "", email: "", mm_username: "" };
  let passwordForm = { old_password: "", new_password: "" };
  let createForm = {
    user_name: "",
    clear_name: "",
    email: "",
    user_pw: "",
    user_group: "user",
    musician: false,
    is_singer: false,
    mm_username: "",
    status: "active",
  };

  async function loadAll() {
    error = "";
    ok = "";
    try {
      me = await getUser();
      selfForm = {
        clear_name: me.clear_name ?? "",
        email: me.email ?? "",
        mm_username: me.mm_username ?? "",
      };
      if (me.user_group === "admin") {
        allUsers = await adminGetAllUsers();
      }
    } catch (err) {
      error = err.message;
      goto("/");
    }
  }

  async function saveSelf() {
    error = "";
    ok = "";
    try {
      me = await updateUser(selfForm);
      ok = "Profil aktualisiert";
    } catch (err) {
      error = err.message;
    }
  }

  async function savePassword() {
    error = "";
    ok = "";
    try {
      await changePasswordByUser({
        user_id: me.id,
        old_password: passwordForm.old_password,
        new_password: passwordForm.new_password,
      });
      passwordForm = { old_password: "", new_password: "" };
      ok = "Passwort geändert";
    } catch (err) {
      error = err.message;
    }
  }

  async function createUser() {
    error = "";
    ok = "";
    try {
      await adminCreateUser(createForm);
      createForm = {
        user_name: "",
        clear_name: "",
        email: "",
        user_pw: "",
        user_group: "user",
        musician: false,
        is_singer: false,
        mm_username: "",
        status: "active",
      };
      allUsers = await adminGetAllUsers();
      ok = "Benutzer angelegt";
    } catch (err) {
      error = err.message;
    }
  }

  async function saveAdminUser(user) {
    error = "";
    ok = "";
    try {
      await adminUpdateUser(user);
      ok = `Benutzer ${user.user_name} aktualisiert`;
      allUsers = await adminGetAllUsers();
    } catch (err) {
      error = err.message;
    }
  }

  async function toggleActive(user) {
    error = "";
    ok = "";
    try {
      if (user.status === "active") {
        await adminDeactivateUser(user.id);
      } else {
        await adminActivateUser(user.id);
      }
      allUsers = await adminGetAllUsers();
    } catch (err) {
      error = err.message;
    }
  }

  async function triggerReset(user) {
    error = "";
    ok = "";
    try {
      await triggerSendPwResetToken(user.id);
      ok = `Passwort-Reset für ${user.user_name} ausgelöst`;
    } catch (err) {
      error = err.message;
    }
  }

  onMount(loadAll);
</script>

{#if me}
  <section class="card">
    <h2>Eigenes Profil</h2>
    <div class="row">
      <div style="flex: 1 1 220px">
        <label for="self-clear-name">Klarname</label>
        <input id="self-clear-name" bind:value={selfForm.clear_name} />
      </div>
      <div style="flex: 1 1 220px">
        <label for="self-email">E-Mail</label>
        <input id="self-email" type="email" bind:value={selfForm.email} />
      </div>
      <div style="flex: 1 1 220px">
        <label for="self-mm">Mattermost Username</label>
        <input id="self-mm" bind:value={selfForm.mm_username} />
      </div>
    </div>
    <p><button onclick={saveSelf}>Profil speichern</button></p>
  </section>

  <section class="card">
    <h2>Passwort ändern</h2>
    <div class="row">
      <div style="flex: 1 1 220px">
        <label for="old-password">Altes Passwort</label>
        <input id="old-password" type="password" bind:value={passwordForm.old_password} />
      </div>
      <div style="flex: 1 1 220px">
        <label for="new-password">Neues Passwort</label>
        <input id="new-password" type="password" bind:value={passwordForm.new_password} />
      </div>
    </div>
    <p><button onclick={savePassword}>Passwort speichern</button></p>
  </section>
{/if}

{#if me && me.user_group === "admin"}
  <section class="card">
    <h2>Benutzer anlegen</h2>
    <div class="row">
      <input placeholder="Username" bind:value={createForm.user_name} />
      <input placeholder="Klarname" bind:value={createForm.clear_name} />
      <input placeholder="E-Mail" type="email" bind:value={createForm.email} />
      <input placeholder="Passwort" type="password" bind:value={createForm.user_pw} />
      <select bind:value={createForm.user_group}>
        <option value="user">user</option>
        <option value="editor">editor</option>
        <option value="admin">admin</option>
      </select>
      <select bind:value={createForm.status}>
        <option value="active">active</option>
        <option value="deactivated">deactivated</option>
      </select>
    </div>
    <p><button onclick={createUser}>Benutzer anlegen</button></p>
  </section>

  <section class="card">
    <h2>Alle Benutzer</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Username</th>
            <th>Klarname</th>
            <th>E-Mail</th>
            <th>Rolle</th>
            <th>Status</th>
            <th>Aktion</th>
          </tr>
        </thead>
        <tbody>
          {#each allUsers as user}
            <tr>
              <td>{user.id}</td>
              <td><input bind:value={user.user_name} /></td>
              <td><input bind:value={user.clear_name} /></td>
              <td><input bind:value={user.email} /></td>
              <td>
                <select bind:value={user.user_group}>
                  <option value="user">user</option>
                  <option value="editor">editor</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>
                <select bind:value={user.status}>
                  <option value="active">active</option>
                  <option value="deactivated">deactivated</option>
                </select>
              </td>
              <td>
                <div class="row">
                  <button onclick={() => saveAdminUser(user)}>Speichern</button>
                  <button class="secondary" onclick={() => triggerReset(user)}>Reset-Link</button>
                  <button class="warn" onclick={() => toggleActive(user)}>
                    {user.status === "active" ? "Deaktivieren" : "Aktivieren"}
                  </button>
                </div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

{/if}
