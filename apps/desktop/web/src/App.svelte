<script lang="ts">
  // AuraAudio desktop shell — hash router: `#/` -> Home, `#/project/{id}` -> ScoreView.
  import Home from "./components/Home.svelte";
  import ScoreView from "./components/ScoreView.svelte";

  const PROJECT_PREFIX = "#/project/";

  function currentHash(): string {
    return window.location.hash || "#/";
  }

  let hash = $state(currentHash());

  $effect(() => {
    const onHashChange = () => {
      hash = currentHash();
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  });

  let projectId = $derived(hash.startsWith(PROJECT_PREFIX) ? hash.slice(PROJECT_PREFIX.length) : null);
</script>

{#if projectId}
  <ScoreView {projectId} />
{:else}
  <Home />
{/if}
