<script>
  import "../app.css";
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import NewSongFromPdfModal from "$lib/components/NewSongFromPdfModal.svelte";
  import { getUser, logout } from "$lib/api.js";
  import { toasts } from "$lib/toasts.js";
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
    <a href="/" class="app-header__brand" onclick={closeMenu} aria-label="Zur Startseite">
      <span class="app-header__logo" aria-hidden="true">Logo</span>
      <strong class="app-header__title">libre-stage User Service</strong>
    </a>
    <div class="app-header__actions">
      <NewSongFromPdfModal canManage={user?.user_group === "admin" || user?.user_group === "editor"} onOpen={closeMenu} />
      <button
        class="secondary app-header__burger"
        onclick={() => (isMenuOpen = !isMenuOpen)}
        aria-label={isMenuOpen ? "Menü schließen" : "Menü öffnen"}
        title={isMenuOpen ? "Menü schließen" : "Menü öffnen"}
      >
        {isMenuOpen ? "✕" : "☰"}
      </button>
    </div>
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
          <a href="/collections" onclick={closeMenu}>Sammlungen</a>
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

{#if $toasts.length > 0}
  <div class="toast-stack" aria-live="polite" aria-atomic="true">
    {#each $toasts as toast (toast.id)}
      <div class={`toast ${toast.type === "error" ? "toast-error" : "toast-ok"}`}>
        {toast.message}
      </div>
    {/each}
  </div>
{/if}

<style>
  .app-header__brand {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    color: inherit;
    text-decoration: none;
  }

  .app-header__logo {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 2.2rem;
    height: 1.5rem;
    padding: 0 0.35rem;
    border-radius: 0.35rem;
    border: 1px solid color-mix(in oklab, currentColor 35%, transparent);
    background: color-mix(in oklab, currentColor 12%, transparent);
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1;
  }

  .app-header__burger {
    min-width: 2rem;
    padding: 0.15rem 0.45rem;
    line-height: 1;
    font-size: 0.9rem;
  }

  .app-header__actions {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }

  .app-header__menu {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    padding: 0 0.65rem 0.65rem 0.65rem;
  }

  .app-header__menu .app-header__meta {
    margin-bottom: 0.1rem;
  }

  .toast-stack {
    position: fixed;
    top: 0.75rem;
    right: 0.75rem;
    z-index: 1200;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    pointer-events: none;
  }

  .toast {
    padding: 0.45rem 0.6rem;
    border-radius: 0.5rem;
    border: 1px solid;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.16);
    font-size: 0.86rem;
    line-height: 1.25;
    max-width: min(90vw, 360px);
  }

  .toast-ok {
    background: var(--color-success-50);
    border-color: var(--color-success-300);
    color: var(--color-success-800);
  }

  .toast-error {
    background: var(--color-error-50);
    border-color: var(--color-error-300);
    color: var(--color-error-800);
  }
</style>
