import "@testing-library/jest-dom/vitest";

// jsdom ne fournit pas ResizeObserver (utilisé par Recharts ResponsiveContainer)
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom ne fournit pas non plus scrollIntoView (utilisé par la page Copilote)
Element.prototype.scrollIntoView = () => {};
