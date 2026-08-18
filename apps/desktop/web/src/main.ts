import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'

// Normalize the initial URL so the hash router (see App.svelte) always has
// a route to match, even on a fresh load with no hash at all.
if (!window.location.hash) {
  window.location.hash = '#/'
}

const app = mount(App, {
  target: document.getElementById('app')!,
})

export default app
