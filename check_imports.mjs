try {
  await import('./app/static/app/state.js');
  console.log('Successfully imported state.js');
} catch (e) {
  console.error('Import error:', e);
}
