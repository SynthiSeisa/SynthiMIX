const e = require('electron');
console.log('type:', typeof e);
console.log('val:', String(e).slice(0,50));

// Try direct built-in lookup
const builtinMods = process.moduleLoadList ? process.moduleLoadList.filter(m=>m.includes('electron')) : [];
console.log('builtins:', builtinMods.join(','));

// Try accessing app via process
if (typeof process.electronBinding === 'function') {
  console.log('electronBinding exists');
}
process.exit(0);
