// Ask again every so often, but only while somebody can see the answer (US-42).
//
// `work` is called every `every` milliseconds while the view is wanted (`start`) and the tab is
// visible. When the tab becomes visible again the work is done at once, because what is on the
// screen may be old; while it is hidden no timer runs and nothing is asked. A call that is still
// under way is not started a second time, so a slow server is not asked faster than it answers.
// `work` may return false to say that asking again is pointless (the person is not signed in):
// the refresh then stops until `start` is called again.
export function createLive({ work, every, doc = document }) {
  let timer = null;
  let wanted = false;
  let busy = false;

  const visible = () => doc.visibilityState === "visible";

  async function tick() {
    if (busy || !wanted || !visible()) return;
    busy = true;
    try {
      if ((await work()) === false) stop();
    } catch (error) {
      // a failed round is the work's to report; the next round tries again
    } finally {
      busy = false;
    }
  }

  function arm() {
    disarm();
    if (wanted && visible()) timer = setInterval(tick, every);
  }

  function disarm() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  function start() {
    wanted = true;
    arm();
  }

  function stop() {
    wanted = false;
    disarm();
  }

  doc.addEventListener("visibilitychange", () => {
    if (!wanted) return;
    if (visible()) {
      tick();
      arm();
    } else {
      disarm();
    }
  });

  return { start, stop, every };
}
