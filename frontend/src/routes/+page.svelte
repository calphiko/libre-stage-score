<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { getUser, login } from "$lib/api.js";

  let username = "";
  let password = "";
  let error = "";
  let loading = false;

  onMount(async () => {
    try {
      await getUser();
      goto("/users");
    } catch (_err) {}
  });

  async function doLogin(event) {
    event.preventDefault();
    loading = true;
    error = "";
    try {
      await login(username, password);
      goto("/users");
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }
</script>

<section class="card">
  <h1>Login</h1>
  <form onsubmit={doLogin}>
    <p>
      <label for="username">Benutzername</label>
      <input id="username" bind:value={username} required />
    </p>
    <p>
      <label for="password">Passwort</label>
      <input id="password" type="password" bind:value={password} required />
    </p>
    <button type="submit" disabled={loading}>{loading ? "..." : "Einloggen"}</button>
  </form>
  {#if error}
    <p class="error">{error}</p>
  {/if}
</section>

