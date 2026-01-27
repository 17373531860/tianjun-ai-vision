export function formatDate(date) {
  return new Date(date).toLocaleString();
}

export function formatCoordinate(x, y) {
  return `(${x.toFixed(2)}, ${y.toFixed(2)})`;
}
