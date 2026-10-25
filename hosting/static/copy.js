// Copy buttons: [data-copy] copies the text of the element with that id.
// Hidden until this runs, so there's no dead button without JavaScript.
for (const button of document.querySelectorAll("[data-copy]")) {
  const target = document.getElementById(button.dataset.copy);
  const label = button.textContent;
  button.hidden = false;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      button.textContent = "Copied ✓";
      button.classList.add("copied");
    } catch {
      // No clipboard (not a secure page, or refused): select it instead.
      const range = document.createRange();
      range.selectNodeContents(target);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      button.textContent = "Press Ctrl+C";
    }
    setTimeout(() => {
      button.textContent = label;
      button.classList.remove("copied");
    }, 2500);
  });
}
