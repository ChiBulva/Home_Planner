(function () {
  function connect() {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${window.location.host}/ws`);
    socket.addEventListener("message", function (event) {
      if (event.data === "tasks_changed") {
        document.body.dispatchEvent(new Event("tasks-changed"));
      }
    });
    socket.addEventListener("close", function () {
      window.setTimeout(connect, 2000);
    });
  }
  connect();

  window.addEventListener("DOMContentLoaded", function () {
    bindSettingsModal();
    const type = document.querySelector("[data-task-type]");
    const reset = document.querySelector("[data-reset-frequency]");
    if (!type) {
      updateEditorSections();
      return;
    }
    let resetTouched = false;
    if (reset) {
      reset.addEventListener("change", function () {
        resetTouched = true;
      });
    }
    type.addEventListener("change", function () {
      if (!reset || resetTouched) {
        updateEditorSections();
        return;
      }
      reset.value = type.value === "chore" ? "daily" : "none";
      updateEditorSections();
    });
    document.querySelectorAll("[data-weekday-toggle]").forEach(function (toggle) {
      toggle.addEventListener("change", updateEditorSections);
    });
    updateEditorSections();
  });

  function updateEditorSections() {
    const type = document.querySelector("[data-task-type]");
    if (!type) {
      return;
    }
    document.querySelectorAll("[data-editor-section]").forEach(function (section) {
      const hidden = section.dataset.editorSection !== type.value;
      section.hidden = hidden;
      section.querySelectorAll("input, select, textarea, button").forEach(function (field) {
        if (hidden) {
          field.disabled = true;
        } else if (!field.closest("[data-weekday-picker]")) {
          field.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-weekday-picker]").forEach(function (picker) {
      const toggle = document.querySelector("[data-weekday-toggle]");
      const hidden = !toggle || !toggle.checked || type.value !== "chore";
      picker.hidden = hidden;
      picker.querySelectorAll("input").forEach(function (field) {
        field.disabled = hidden;
      });
    });
  }

  function bindSettingsModal() {
    const modal = document.querySelector("[data-settings-modal]");
    const open = document.querySelector("[data-settings-open]");
    if (!modal || !open) {
      return;
    }
    const closeAll = function () {
      modal.hidden = true;
    };
    open.addEventListener("click", function () {
      modal.hidden = false;
    });
    modal.querySelectorAll("[data-settings-close]").forEach(function (node) {
      node.addEventListener("click", closeAll);
    });
    window.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) {
        closeAll();
      }
    });
  }
})();
