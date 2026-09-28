<script>
  import { page } from "$app/stores";
  import { verifyPwResetToken, resetPassword } from "$lib/api.js";
  import { onMount } from "svelte";

  let token = "";
  let userName = "";
  let newPassword = "";
  let error = "";
  let ok = "";
  let tokenValid = false;

  onMount(async () => {
    token = $page.url.searchParams.get("token") ?? "";
    if (!token) {
      error = "Reset-Link ist ungültig";
      return;
    }
    try {
      const payload = await verifyPwResetToken(token);
      userName = payload.user_name;
      tokenValid = true;
    } catch (err) {
      error = err.message;
    }
  });

  async function doReset(event) {
    event.preventDefault();
    error = "";
    ok = "";
    try {
      await resetPassword(token, newPassword);
      ok = "Passwort wurde zurückgesetzt";
      newPassword = "";
    } catch (err) {
      error = err.message;
    }
  }
</script>

<section class="card">
  <h1>Passwort zurücksetzen</h1>
  {#if error}
    <p class="error">{error}</p>
  {/if}
  {#if ok}
    <p class="ok">{ok}</p>
  {/if}

  {#if tokenValid}
    <p>Benutzer: <strong>{userName}</strong></p>
    <form onsubmit={doReset}>
      <label for="new-password">Neues Passwort</label>
      <input id="new-password" type="password" bind:value={newPassword} required />
      <p><button type="submit">Passwort setzen</button></p>
    </form>
  {/if}
</section>

