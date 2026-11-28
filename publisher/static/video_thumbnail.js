// Video page: upload or replace the thumbnail, then reload to show it.
(function () {
  const form = document.getElementById("thumb-form");
  if (!form) return;

  const videoId = form.dataset.videoId;
  const allowed = JSON.parse(form.dataset.types);
  const maxBytes = parseInt(form.dataset.maxMb, 10) * 1024 * 1024;

  const input = document.getElementById("thumb-file");
  const button = document.getElementById("thumb-button");
  const message = document.getElementById("thumb-message");

  function showMessage(text) {
    message.textContent = text;
    message.hidden = !text;
  }

  input.addEventListener("change", () => {
    showMessage("");
    button.disabled = true;
    const file = input.files[0];
    if (!file) return;
    if (!allowed.includes(file.type)) {
      showMessage("Thumbnails must be JPEG or PNG.");
      return;
    }
    if (file.size > maxBytes) {
      showMessage("This image is too large (max " + Math.round(maxBytes / 1048576) + " MB).");
      return;
    }
    button.disabled = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = input.files[0];
    if (!file) return;
    button.disabled = true;
    input.disabled = true;
    button.textContent = "Uploading...";
    showMessage("");
    try {
      await window.Uploader.run(file, {
        presignUrl: "/videos/" + videoId + "/thumbnail/presign",
        confirmUrl: "/videos/" + videoId + "/thumbnail/confirm",
      });
      window.location.reload();
    } catch (error) {
      showMessage(error.message);
      button.textContent = "Upload thumbnail";
      input.disabled = false;
      button.disabled = false;
    }
  });
})();
