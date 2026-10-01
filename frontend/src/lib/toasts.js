import { writable } from "svelte/store";

const DEFAULT_TIMEOUT_MS = 3200;

function createToastStore() {
  const { subscribe, update } = writable([]);

  return {
    subscribe,
    push(message, type = "ok", timeout = DEFAULT_TIMEOUT_MS) {
      const id = `${Date.now()}-${Math.random()}`;
      update((items) => [...items, { id, message, type }]);
      setTimeout(() => {
        update((items) => items.filter((item) => item.id !== id));
      }, timeout);
    },
  };
}

export const toasts = createToastStore();

export function showToast(message, type = "ok", timeout = DEFAULT_TIMEOUT_MS) {
  toasts.push(message, type, timeout);
}
