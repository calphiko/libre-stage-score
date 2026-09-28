<script>
  import "../app.css";
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import { getUser, logout } from "$lib/api.js";
  import { applyTheme, getStoredTheme, skeletonThemes } from "$lib/theme";

  let user = null;
  let selectedTheme = getStoredTheme();

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

  function onThemeChange(event) {
    const theme = event.currentTarget.value;
    selectedTheme = theme;
    applyTheme(theme);
  }

  onMount(() => {
    refreshUser();
    applyTheme(selectedTheme);
  });
</script>

<header class="app-header">
  <div class="app-header__inner">
    <strong class="app-header__title">libre-stage User Service</strong>
    <div class="app-header__nav">
      {#if user}
        <span class="app-header__meta">Eingeloggt als: {user.user_name} ({user.user_group})</span>
        <a href="/users">Benutzerverwaltung</a>
        <a href="/songs">Stücke & Stimmen</a>
        <div class="theme-switch">
          <label class="label-text" for="theme-select">Theme</label>
          <select id="theme-select" bind:value={selectedTheme} onchange={onThemeChange} aria-label="Theme auswählen">
            {#each skeletonThemes as theme}
              <option value={theme}>{theme}</option>
            {/each}
          </select>
        </div>
        <button class="secondary" onclick={doLogout}>Logout</button>
      {:else}
        <a href="/">Login</a>
      {/if}
    </div>
  </div>
</header>

<main>
  <slot />
</main>
