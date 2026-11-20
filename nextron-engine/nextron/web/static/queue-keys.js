// The Approval Queue's keyboard shortcuts (Story 5.4; Story 5.6 adds the editor; Story 5.7 adds
// the rest of the keymap). Self-hosted and loaded only by queue.html (defer): no inline script,
// no eval, no on* handler. One keymap table maps a key (with "ctrl+" for Ctrl) to an action.
// A plain key is ignored with Ctrl, Alt, Meta or Shift held, when repeated, or while focus is in
// an input, textarea, select or contenteditable element. The editor's keys (Ctrl+Enter submits,
// Esc resets and closes it, then focus returns to the card) work only inside the edit form.
(function () {
  "use strict";

  var KEYMAP = {
    a: "approve",
    e: "edit",
    "ctrl+enter": "submitEdit",
    escape: "cancelEdit"
  };

  // The actions that run only while focus is inside the edit form.
  var IN_EDITOR = {
    submitEdit: true,
    cancelEdit: true
  };

  function submit(form, button) {
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit(button || undefined);
    } else if (button) {
      button.click();
    } else {
      return false;
    }
    return true;
  }

  function editorOf(target) {
    if (!target || target.nodeType !== 1 || typeof target.closest !== "function") {
      return null;
    }
    return target.closest("form.queue-edit-form");
  }

  var ACTIONS = {
    // Submit the Approve form when the focused card has an enabled Approve button.
    approve: function () {
      var button = document.querySelector(
        "form.queue-approve-form button.queue-approve[type=submit]:not([disabled])"
      );
      if (!button || !button.form) {
        return false;
      }
      return submit(button.form, button);
    },
    // Open the card's editor and focus its subject.
    edit: function () {
      var details = document.querySelector("details.queue-edit");
      if (!details) {
        return false;
      }
      details.open = true;
      var subject = details.querySelector("input.queue-edit-subject");
      if (subject) {
        subject.focus();
      }
      return true;
    },
    // Ctrl+Enter inside the editor: save the edit (then approve once the recheck passes).
    submitEdit: function (form) {
      var button = form.querySelector("button.queue-edit-submit[type=submit]");
      return submit(form, button);
    },
    // Esc inside the editor: drop the changes, close it and return focus to the card.
    cancelEdit: function (form) {
      form.reset();
      var details = form.closest("details.queue-edit");
      if (details) {
        details.open = false;
      }
      var card = document.querySelector("article.queue-card");
      if (card && typeof card.focus === "function") {
        card.focus();
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
    if (event.altKey || event.metaKey || event.shiftKey) {
      return;
    }
    var key = typeof event.key === "string" ? event.key.toLowerCase() : "";
    var combo = (event.ctrlKey ? "ctrl+" : "") + key;
    var name = Object.prototype.hasOwnProperty.call(KEYMAP, combo) ? KEYMAP[combo] : null;
    if (!name) {
      return;
    }
    var done;
    if (IN_EDITOR[name]) {
      var form = editorOf(event.target);
      if (!form) {
        return;
      }
      done = ACTIONS[name](form);
    } else {
      if (event.ctrlKey || typing(event.target)) {
        return;
      }
      done = ACTIONS[name]();
    }
    if (done) {
      event.preventDefault();
    }
  });
})();
