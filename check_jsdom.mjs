import { JSDOM } from 'jsdom';
const dom = new JSDOM(\`<!DOCTYPE html><html><body><div id="wizard-root"></div><main id="app"></main></body></html>\`, {
  url: "http://localhost:8000/",
  runScripts: "dangerously",
  resources: "usable"
});
dom.window.console = console;

dom.window.eval(\`
  import('./app/static/app/state.js').then(() => {
    console.log("Loaded!");
  }).catch(e => {
    console.error("Error loading:", e);
  });
\`);
