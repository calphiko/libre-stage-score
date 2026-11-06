<script>
  import { goto } from "$app/navigation";
  import { onMount } from "svelte";
  import {
    createSyncPeer,
    deleteSyncPeer,
    getSyncBlobStatus,
    getSyncConflicts,
    getSyncStatus,
    getUser,
    resolveSyncConflict,
    updateSyncPeer,
  } from "$lib/api.js";

  let me = null;
  let status = null;
  let conflicts = [];
  let missingBlobs = [];
  let error = "";
  let success = "";
  let loading = false;
  let savingPeer = false;
  let peerForm = { peer_id: "", url: "", credentials_reference: "" };

  async function loadDashboard() {
    loading = true;
    error = "";
    try {
      const [nextStatus, nextConflicts, nextBlobs] = await Promise.all([
        getSyncStatus(),
        getSyncConflicts(),
        getSyncBlobStatus(),
      ]);
      status = nextStatus;
      conflicts = nextConflicts;
      missingBlobs = nextBlobs;
    } catch (err) {
      error = err.message;
    } finally {
      loading = false;
    }
  }

  async function initialize() {
    try {
      me = await getUser();
      if (me.user_group !== "admin") {
        goto("/songs");
        return;
      }
      await loadDashboard();
    } catch (_err) {
      goto("/");
    }
  }

  async function addPeer(event) {
    event.preventDefault();
    error = "";
    success = "";
    savingPeer = true;
    try {
      await createSyncPeer(peerForm);
      peerForm = { peer_id: "", url: "", credentials_reference: "" };
      success = "Peer hinzugefügt";
      await loadDashboard();
    } catch (err) {
      error = err.message;
    } finally {
      savingPeer = false;
    }
  }

  async function togglePeer(peer) {
    error = "";
    success = "";
    try {
      await updateSyncPeer(peer.peer_id, { active: !peer.active });
      success = peer.active ? "Peer pausiert" : "Peer aktiviert";
      await loadDashboard();
    } catch (err) {
      error = err.message;
    }
  }

  async function removePeer(peer) {
    if (!window.confirm(`Peer ${peer.peer_id} wirklich entfernen?`)) return;
    error = "";
    success = "";
    try {
      await deleteSyncPeer(peer.peer_id);
      success = "Peer entfernt";
      await loadDashboard();
    } catch (err) {
      error = err.message;
    }
  }

  async function markConflictResolved(conflict) {
    error = "";
    success = "";
    try {
      await resolveSyncConflict(conflict.conflict_id, "Manuell geprüft");
      success = "Konflikt als geklärt markiert";
      await loadDashboard();
    } catch (err) {
      error = err.message;
    }
  }

  function formatDate(value) {
    if (!value) return "Noch kein erfolgreicher Abgleich";
    return new Date(value).toLocaleString();
  }

  onMount(() => {
    initialize();
    const timer = window.setInterval(() => {
      if (me?.user_group === "admin") loadDashboard();
    }, 30_000);
    return () => window.clearInterval(timer);
  });
</script>

<svelte:head>
  <title>Synchronisation verwalten</title>
</svelte:head>

{#if me?.user_group === "admin"}
  <section class="card">
    <div class="section-heading">
      <div>
        <h1>Synchronisation</h1>
        <p>Lokaler Status und Verbindung zu den konfigurierten Instanzen.</p>
      </div>
      <button class="secondary" onclick={loadDashboard} disabled={loading}>
        {loading ? "Aktualisiere…" : "Aktualisieren"}
      </button>
    </div>

    {#if error}<p class="message error" role="alert">{error}</p>{/if}
    {#if success}<p class="message success" role="status">{success}</p>{/if}

    {#if status}
      {#if !status.enabled}
        <p class="message warning">Der Sync-Worker ist in dieser Instanz deaktiviert (SYNC_ENABLED=false).</p>
      {/if}
      <div class="status-grid">
        <div><strong>Worker</strong><span>{status.enabled ? "Aktiviert" : "Deaktiviert"}</span></div>
        <div><strong>Ausstehende Änderungen</strong><span>{status.pending_changes}</span></div>
        <div><strong>Offene Konflikte</strong><span>{status.open_conflicts}</span></div>
        <div><strong>Letzte Änderung</strong><span>#{status.latest_change}</span></div>
      </div>
      <h2>Peers</h2>
      {#if status.peers.length}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Peer</th>
                <th>Status</th>
                <th>Letzter Abgleich</th>
                <th>Cursor (gesendet / empfangen)</th>
                <th>Aktionen</th>
              </tr>
            </thead>
            <tbody>
              {#each status.peers as peer (peer.peer_id)}
                <tr>
                  <td>
                    <strong>{peer.peer_id}</strong><br />
                    <span class="muted">{peer.url}</span>
                  </td>
                  <td>
                    <span class={`state state-${peer.reachability}`}>
                      {peer.reachability === "reachable" ? "Erreichbar" : peer.reachability === "unreachable" ? "Nicht erreichbar" : peer.reachability === "paused" ? "Pausiert" : peer.reachability === "not_configured" ? "Nicht in SYNC_PEERS" : "Noch unbekannt"}
                    </span>
                  </td>
                  <td>{formatDate(peer.last_successful_sync)}</td>
                  <td>{peer.last_sent} / {peer.last_received}</td>
                  <td class="actions">
                    <button class="secondary" onclick={() => togglePeer(peer)}>
                      {peer.active ? "Pausieren" : "Aktivieren"}
                    </button>
                    <button class="warn" onclick={() => removePeer(peer)}>Entfernen</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p>Es sind noch keine Peers eingetragen.</p>
      {/if}
    {/if}
  </section>

  <section class="card">
    <h2>Peer hinzufügen</h2>
    <p>Das gemeinsame Geheimnis bleibt in den Umgebungsvariablen; hier werden nur die Variablennamen hinterlegt.</p>
    <form onsubmit={addPeer}>
      <div class="form-grid">
        <label for="peer-id">Node-UUID des Gegenübers
          <input id="peer-id" bind:value={peerForm.peer_id} required minlength="36" maxlength="36" />
        </label>
        <label for="peer-url">Basis-URL
          <input id="peer-url" bind:value={peerForm.url} type="url" placeholder="https://musik.example.org" required />
        </label>
        <label for="peer-secret">Schlüssel-Umgebungsvariable(n)
          <input id="peer-secret" bind:value={peerForm.credentials_reference} placeholder="PEER_KEY" required />
        </label>
      </div>
      <button type="submit" disabled={savingPeer}>{savingPeer ? "Wird gespeichert…" : "Peer hinzufügen"}</button>
    </form>
  </section>

  <section class="card">
    <h2>Offene Konflikte</h2>
    <p>Die Auflösung hier quittiert einen manuell geprüften Konflikt; sie ändert keine Datensätze.</p>
    {#if conflicts.length}
      <div class="table-wrap">
        <table>
          <thead><tr><th>Erkannt</th><th>Tabelle / UUID</th><th>Ursache</th><th>Remote-Node</th><th></th></tr></thead>
          <tbody>
            {#each conflicts as conflict (conflict.conflict_id)}
              <tr>
                <td>{formatDate(conflict.detected_at)}</td>
                <td>{conflict.table_name}<br /><span class="muted">{conflict.record_uuid}</span></td>
                <td>{conflict.reason}</td>
                <td>{conflict.remote_node_id}</td>
                <td><button class="secondary" onclick={() => markConflictResolved(conflict)}>Als geklärt markieren</button></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p>Keine offenen Konflikte.</p>
    {/if}
  </section>

  <section class="card">
    <h2>Nicht lokal verfügbare PDFs</h2>
    {#if missingBlobs.length}
      <div class="table-wrap">
        <table>
          <thead><tr><th>SHA-256</th><th>Status</th><th>Größe</th><th>Betroffene Scores</th></tr></thead>
          <tbody>
            {#each missingBlobs as blob (blob.sha256)}
              <tr>
                <td><code>{blob.sha256}</code></td>
                <td>PDF noch nicht lokal verfügbar ({blob.status})</td>
                <td>{blob.size_bytes ?? "Unbekannt"}</td>
                <td>{blob.score_ids.join(", ")}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p>Alle referenzierten PDFs sind lokal verfügbar.</p>
    {/if}
  </section>
{/if}

<style>
  .section-heading {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
  }

  .section-heading p {
    margin-top: 0;
  }

  .status-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 0.6rem;
    margin: 0.8rem 0 1.2rem;
  }

  .status-grid > div {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    padding: 0.65rem;
    border: 1px solid color-mix(in oklab, currentColor 18%, transparent);
    border-radius: 0.45rem;
  }

  .form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.7rem;
    margin-bottom: 0.8rem;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .state {
    font-weight: 600;
  }

  .state-reachable,
  .state-paused {
    color: var(--color-success-300);
  }

  .state-unreachable {
    color: var(--color-error-300);
  }

  .state-unknown,
  .muted {
    opacity: 0.7;
  }

  .message {
    padding: 0.55rem 0.7rem;
    border-radius: 0.4rem;
  }

  .error,
  .warning {
    background: color-mix(in oklab, var(--color-error-500) 14%, transparent);
  }

  .success {
    background: color-mix(in oklab, var(--color-success-500) 14%, transparent);
  }

  code {
    overflow-wrap: anywhere;
  }
</style>
