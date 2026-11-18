// How the page writes what the core sends: a time as a person says it, a duration, a run number.
// Pure functions; the clock is a parameter, so a test can give a fixed one.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

// The first 8 characters of a run number; the whole one is in the tooltip and is what a click copies.
export function shortId(id) {
  return String(id).slice(0, 8);
}

// "just now", "5 min ago", "2 h ago" within a day; after that the date, "12 Nov, 10:10" (and the year
// when it is not this one). A time that cannot be read is shown as it came.
export function when(text, now = Date.now()) {
  if (!text) return "";
  const moment = new Date(text);
  if (Number.isNaN(moment.getTime())) return String(text);
  const seconds = Math.round((now - moment.getTime()) / 1000);
  if (seconds >= 0 && seconds < 60) return "just now";
  if (seconds >= 0 && seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds >= 0 && seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  const year = moment.getFullYear() === new Date(now).getFullYear() ? "" : ` ${moment.getFullYear()}`;
  return `${moment.getDate()} ${MONTHS[moment.getMonth()]}${year}, ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

// "8.4 s", "14 min", "2 h 5 min"; nothing when there is no duration.
export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${rest} min` : `${Math.floor(minutes / 60)} h`;
}

// "1 run", "3 runs".
export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// The time of day for a greeting, in the person's own time.
export function greeting(now = new Date()) {
  const hour = now.getHours();
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// A run number that copies itself to the clipboard when it is clicked.
export function copyable(element, full) {
  element.title = `${full} (click to copy)`;
  element.classList.add("copyable");
  element.addEventListener("click", async (event) => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(full);
      element.dataset.copied = "true";
      setTimeout(() => delete element.dataset.copied, 1200);
    } catch {
      /* a browser that refuses the clipboard: the number is still in the tooltip */
    }
  });
  return element;
}
