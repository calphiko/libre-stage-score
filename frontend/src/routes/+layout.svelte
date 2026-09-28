<script>
  import "../app.css";
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { getUser, logout } from "$lib/api.js";

  let user = null;

  async function refreshUser() {
    try {
      user = await getUser();
    } catch (_err) {
      user = null;
    }
  }

  async function doLogout() {
    await logout();
    user = null;
    goto("/");
  }

  onMount(refreshUser);
</script>

<header class="card" style="border-radius: 0; margin-bottom: 0; border-left: 0; border-right: 0; border-top: 0; background-color: var(--color-primary-500); color: var(--color-primary-contrast-500);">
  <div class="row" style="justify-content: space-between; align-items: center; max-width: 980px; margin: 0 auto;">
    <strong>libre-stage User Service</strong>
    <div class="row">
      {#if user}
        <span style="opacity: 0.85;">Eingeloggt als: {user.user_name} ({user.user_group})</span>
        <a href="/users" style="color: var(--color-primary-contrast-500);">Benutzerverwaltung</a>
        <a href="/songs" style="color: var(--color-primary-contrast-500);">Stücke & Stimmen</a>
        <button onclick={doLogout} style="background: oklch(from var(--color-primary-500) l c h / 0.25); color: var(--color-primary-contrast-500); border: 1px solid oklch(from var(--color-primary-contrast-500) l c h / 0.4);">Logout</button>
      {:else}
        <a href="/" style="color: var(--color-primary-contrast-500);">Login</a>
      {/if}
    </div>
  </div>
</header>

<main>
  <slot />
</main>
