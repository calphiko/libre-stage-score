<script>
  import "../app.css";
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { getUser, logout } from "$lib/api.js";
  import { applyTheme, getStoredTheme } from "$lib/theme";

  let user = null;
  let isMenuOpen = false;

  async function refreshUser() {
    try {
      user = await getUser();
    } catch (_err) {
      user = null;
    }
  }

  function closeMenu() {
    isMenuOpen = false;
  }

  async function doLogout() {
    await logout();
    user = null;
    closeMenu();
    goto("/");
  }

  onMount(() => {
    refreshUser();
    applyTheme(getStoredTheme());
    const onAuthChanged = () => {
      closeMenu();
      refreshUser();
    };
    window.addEventListener("auth:changed", onAuthChanged);
    return () => {
      window.removeEventListener("auth:changed", onAuthChanged);
    };
  });
</script>

<header class="app-header">
  <div class="app-header__inner">
    <strong class="app-header__title">libre-stage User Service</strong>
    <button
      class="secondary app-header__burger"
      onclick={() => (isMenuOpen = !isMenuOpen)}
      aria-label={isMenuOpen ? "Menü schließen" : "Menü öffnen"}
      title={isMenuOpen ? "Menü schließen" : "Menü öffnen"}
    >
      {isMenuOpen ? "✕" : "☰"}
    </button>
  </div>
  {#if isMenuOpen}
    <nav class="app-header__menu">
      {#if user}
        <span class="app-header__meta">Eingeloggt als: {user.user_name} ({user.user_group})</span>
        {#if user.user_group === "admin"}
          <a href="/users" onclick={closeMenu}>Benutzerverwaltung</a>
        {/if}
        <a href="/songs" onclick={closeMenu}>Stücke & Stimmen</a>
        {#if user.user_group === "admin" || user.user_group === "editor"}
          <a href="/groups" onclick={closeMenu}>Gruppenverwaltung</a>
          <a href="/instruments" onclick={closeMenu}>Instrumentenverwaltung</a>
        {/if}
        <button class="secondary" onclick={doLogout}>Logout</button>
      {:else}
        <a href="/" onclick={closeMenu}>Login</a>
      {/if}
    </nav>
  {/if}
</header>

<main>
  <slot />
</main>

<style>
  .app-header__burger {
    min-width: 2.25rem;
    padding: 0.2rem 0.55rem;
    line-height: 1;
    font-size: 1rem;
  }

  .app-header__menu {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    padding: 0 1rem 1rem 1rem;
  }

  .app-header__menu .app-header__meta {
    margin-bottom: 0.1rem;
  }
</style>
