<script>
  import "../app.css";
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import NewSongFromPdfModal from "$lib/components/NewSongFromPdfModal.svelte";
  import { getUser, logout } from "$lib/api.js";
  import { showToast, toasts } from "$lib/toasts.js";
  import { applyTheme, getDefaultTheme } from "$lib/theme";

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
    try {
      await logout();
      user = null;
      closeMenu();
      goto("/");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  onMount(() => {
    refreshUser();
    applyTheme(getDefaultTheme());
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
      <span class="app-header__logo" aria-hidden="true"><img src="logo_klein.png" alt="libreStage score" /></span>
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
          <a href="/sync" onclick={closeMenu}>Synchronisation</a>
        {/if}
        <a href="/songs" onclick={closeMenu}>Stücke & Stimmen</a>
        {#if user.user_group === "admin" || user.user_group === "editor"}
          <a href="/groups" onclick={closeMenu}>Gruppenverwaltung</a>
          <a href="/instruments" onclick={closeMenu}>Instrumentenverwaltung</a>
          <a href="/song-metadata" onclick={closeMenu}>Stammdaten & Sammlungen</a>
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
    transform: translateY(-2px);
    color: inherit;
    text-decoration: none;
  }

  .app-header__logo {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 2.2rem;
    box-sizing: border-box;
    height: 50px;
    padding: 0 0.35rem;
    isolation: isolate;
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1;
  }

  .app-header__logo::before {
    position: absolute;
    z-index: -1;
    inset: -0.6rem 0;
    //border-radius: 0.35rem;
    //border: 1px solid color-mix(in oklab, currentColor 35%, transparent);
    //background: color-mix(in oklab, currentColor 12%, transparent);
    content: "";
  }

  .app-header__logo img {
    position: relative;
    z-index: 1;
    width: auto;
    height: 100%;
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
    box-shadow: 0 10px 28px oklch(0 0 0 / 0.28);
    font-size: 0.86rem;
    line-height: 1.25;
    max-width: min(90vw, 360px);
  }

  .toast-ok {
    background: color-mix(in oklab, var(--color-success-900) 78%, var(--color-surface-950));
    border-color: color-mix(in oklab, var(--color-success-500) 55%, transparent);
    color: var(--color-success-200);
  }

  .toast-error {
    background: color-mix(in oklab, var(--color-error-900) 78%, var(--color-surface-950));
    border-color: color-mix(in oklab, var(--color-error-500) 55%, transparent);
    color: var(--color-error-200);
  }
</style>
