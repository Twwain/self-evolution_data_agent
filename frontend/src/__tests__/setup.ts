import "@testing-library/jest-dom/vitest";

/* ── jsdom 缺 matchMedia, antd Table/Grid 内部 useBreakpoint 依赖 ── */
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

/* ── jsdom 25 在 vitest/node 某些启动场景下 localStorage.setItem 丢失
       (node --localstorage-file 警告), 用 Map polyfill 兜底确保单测可用 ── */
if (typeof window !== "undefined" && typeof window.localStorage?.setItem !== "function") {
  const store = new Map<string, string>();
  const fakeStorage: Storage = {
    get length() { return store.size; },
    clear: () => { store.clear(); },
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, String(v)); },
    removeItem: (k: string) => { store.delete(k); },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
  };
  Object.defineProperty(window, "localStorage", { value: fakeStorage, configurable: true });
  Object.defineProperty(globalThis, "localStorage", { value: fakeStorage, configurable: true });
}
