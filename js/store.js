// IndexedDB clé/valeur : favoris, GPX importés, altitudes en cache
const DB = 'randos-hiver', ST = 'kv';
let dbp;
const db = () => dbp ??= new Promise((res, rej) => {
  const r = indexedDB.open(DB, 1);
  r.onupgradeneeded = () => r.result.createObjectStore(ST);
  r.onsuccess = () => res(r.result);
  r.onerror = () => rej(r.error);
});
const tx = async (mode, fn) => {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction(ST, mode), req = fn(t.objectStore(ST));
    t.oncomplete = () => res(req?.result);
    t.onerror = () => rej(t.error);
  });
};
export const get = k => tx('readonly', s => s.get(k));
export const set = (k, v) => tx('readwrite', s => s.put(v, k));
export const del = k => tx('readwrite', s => s.delete(k));
