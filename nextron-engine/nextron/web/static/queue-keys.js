// The Approval Queue's keyboard shortcuts (Story 5.4; Story 5.7 adds the rest of the keymap).
// Self-hosted and loaded only by queue.html (defer): no inline script, no eval, no on* handler.
// One keymap table maps a key to an action. A key is ignored with Ctrl, Alt, Meta or Shift held,
// when repeated, or while focus is in an input, textarea, select or contenteditable element.
(function () {
  "use strict";

  var KEYMAP = {
    a: "approve"
  };

  var ACTIONS = {
    // Submit the Approve form when the focused card has an enabled Approve button.
    approve: function () {
      var button = document.querySelector(
        "form.queue-approve-form button.queue-approve[type=submit]:not([disabled])"
      );
      if (!button || !button.form) {
        return false;
      }
      if (typeof button.form.requestSubmit === "function") {
        button.form.requestSubmit(button);
      } else {
        button.click();
      }
      return true;
    }
  };

  function typing(target) {
    if (!target || target.nodeType !== 1) {
      return false;
    }
    var tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      return true;
    }
    return Boolean(target.isContentEditable);
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.repeat) {
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) {
      return;
    }
    if (typing(event.target)) {
      return;
    }
    var key = typeof event.key === "string" ? event.key.toLowerCase() : "";
    var action = Object.prototype.hasOwnProperty.call(KEYMAP, key) ? ACTIONS[KEYMAP[key]] : null;
    if (action && action()) {
      event.preventDefault();
    }
  });
})();
