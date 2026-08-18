<script lang="ts">
  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();
</script>

<div class="page">
  <a class="back-link" href="#/">&larr; Back to projects</a>
  <h1>Project {projectId}</h1>
  <p class="note">Score view lands in Task 6.</p>
</div>

<style>
  .page {
    min-height: 100vh;
    background: var(--bg);
    color: var(--text);
    padding: 32px clamp(20px, 6vw, 120px);
  }

  .back-link {
    color: var(--accent);
    font-size: 13px;
    text-decoration: none;
  }

  .back-link:hover {
    text-decoration: underline;
  }

  h1 {
    font-size: 20px;
    margin: 20px 0 4px;
  }

  .note {
    color: var(--dim);
    font-size: 13px;
    margin: 0;
  }
</style>
