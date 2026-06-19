const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const ACCEPTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const FIELD_LABELS = {
  brand_name: "Brand Name",
  product_class: "Type of Product",
  producer: "Producer or Bottler Name",
  country: "Country of Origin",
  abv: "Alcohol Percentage (ABV)",
  net_contents: "Container Size (Net Contents)",
  government_warning: "Government Health Warning",
};

const formView = document.getElementById("form-view");
const resultsView = document.getElementById("results-view");
const form = document.getElementById("verification-form");
const fieldset = document.getElementById("verification-fields");
const imageInput = document.getElementById("image");
const imagePreview = document.getElementById("image-preview");
const selectedFile = document.getElementById("selected-file");
const errorSummary = document.getElementById("error-summary");
const loadingStatus = document.getElementById("loading-status");
const submitButton = document.getElementById("submit-button");
const buttonLabel = submitButton.querySelector(".button-label");
const spinner = submitButton.querySelector(".spinner");
const resetButton = document.getElementById("reset-button");

let previewUrl = null;

class DisplayError extends Error {}

imageInput.addEventListener("change", () => {
  clearFieldError("image");
  updateImagePreview();
});

form.addEventListener("input", (event) => {
  if (Object.hasOwn(FIELD_LABELS, event.target.name)) {
    clearFieldError(event.target.name);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors();

  const firstInvalidControl = validateForm();
  if (firstInvalidControl) {
    showErrorSummary("Please correct the highlighted information and try again.");
    firstInvalidControl.focus();
    return;
  }

  setLoading(true);

  try {
    const response = await fetch("/verify", {
      method: "POST",
      body: new FormData(form),
    });

    const payload = await readJson(response);
    if (!response.ok) {
      const message = typeof payload?.message === "string"
        ? payload.message
        : "We could not check this label. Please try again.";
      throw new DisplayError(message);
    }

    if (!isVerificationResult(payload)) {
      throw new Error("Unexpected verification response");
    }

    renderResults(payload);
  } catch (error) {
    const message = error instanceof DisplayError
      ? error.message
      : "We could not check this label. Please check your connection and try again.";
    showErrorSummary(message);
    errorSummary.focus();
  } finally {
    setLoading(false);
  }
});

resetButton.addEventListener("click", resetPage);

function validateForm() {
  let firstInvalidControl = null;
  const file = imageInput.files[0];

  if (!file) {
    firstInvalidControl = markInvalid(imageInput, "Choose a label photo.", firstInvalidControl);
  } else if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    firstInvalidControl = markInvalid(
      imageInput,
      "Choose a JPEG, PNG, or WEBP photo.",
      firstInvalidControl,
    );
  } else if (file.size > MAX_UPLOAD_BYTES) {
    firstInvalidControl = markInvalid(
      imageInput,
      "Choose a photo smaller than 10 MB.",
      firstInvalidControl,
    );
  }

  Object.entries(FIELD_LABELS).forEach(([name, label]) => {
    const control = form.elements.namedItem(name);
    if (!control.value.trim()) {
      firstInvalidControl = markInvalid(
        control,
        `Enter the ${label}.`,
        firstInvalidControl,
      );
    }
  });

  return firstInvalidControl;
}

function markInvalid(control, message, firstInvalidControl) {
  control.setAttribute("aria-invalid", "true");
  document.getElementById(`${control.id}-error`).textContent = message;
  return firstInvalidControl || control;
}

function clearFieldError(name) {
  const control = form.elements.namedItem(name);
  control.removeAttribute("aria-invalid");
  document.getElementById(`${name}-error`).textContent = "";
}

function clearErrors() {
  errorSummary.hidden = true;
  errorSummary.textContent = "";
  ["image", ...Object.keys(FIELD_LABELS)].forEach(clearFieldError);
}

function showErrorSummary(message) {
  errorSummary.textContent = message;
  errorSummary.hidden = false;
}

function updateImagePreview() {
  releasePreviewUrl();
  const file = imageInput.files[0];

  if (!file) {
    selectedFile.textContent = "No photo selected.";
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
    return;
  }

  selectedFile.textContent = `Selected photo: ${file.name}`;
  if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    imagePreview.hidden = true;
    imagePreview.removeAttribute("src");
    return;
  }

  previewUrl = URL.createObjectURL(file);
  imagePreview.src = previewUrl;
  imagePreview.hidden = false;
}

function releasePreviewUrl() {
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
}

function setLoading(isLoading) {
  fieldset.disabled = isLoading;
  buttonLabel.textContent = isLoading ? "Checking Label…" : "Check This Label";
  spinner.hidden = !isLoading;
  loadingStatus.textContent = isLoading ? "Checking the label. Please wait." : "";
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isVerificationResult(payload) {
  return payload
    && (payload.verdict === "PASS" || payload.verdict === "NEEDS_REVIEW")
    && Array.isArray(payload.fields)
    && payload.fields.every((field) => (
      typeof field.field_name === "string"
      && (field.status === "PASS" || field.status === "FAIL")
      && typeof field.expected === "string"
      && typeof field.actual === "string"
    ));
}

function renderResults(result) {
  const failedFields = result.fields.filter((field) => field.status === "FAIL");
  const passedFields = result.fields.filter((field) => field.status === "PASS");
  const approved = result.verdict === "PASS";

  const banner = document.getElementById("verdict-banner");
  const verdict = document.getElementById("result-verdict");
  const summary = document.getElementById("result-summary");
  const failedSection = document.getElementById("failed-section");
  const failedResults = document.getElementById("failed-results");
  const passedSection = document.getElementById("passed-section");
  const passedResults = document.getElementById("passed-results");

  banner.className = `verdict-banner ${approved ? "verdict-approved" : "verdict-review"}`;
  verdict.textContent = approved ? "APPROVED" : "NEEDS REVIEW";
  summary.textContent = approved
    ? "All 7 items match the label."
    : `${failedFields.length} ${failedFields.length === 1 ? "item needs" : "items need"} attention.`;

  failedResults.replaceChildren(...failedFields.map(createFailureCard));
  passedResults.replaceChildren(...passedFields.map(createPassRow));
  failedSection.hidden = failedFields.length === 0;
  passedSection.hidden = passedFields.length === 0;

  formView.hidden = true;
  resultsView.hidden = false;
  window.scrollTo({ top: 0, behavior: "auto" });
  verdict.focus();
}

function createFailureCard(field) {
  const card = document.createElement("article");
  card.className = "result-card result-fail";

  const header = document.createElement("div");
  header.className = "result-card-header";
  const title = document.createElement("h3");
  title.textContent = displayFieldName(field.field_name);
  header.append(title, createStatusBadge("FAIL"));

  const explanation = document.createElement("p");
  explanation.className = "failure-explanation";
  explanation.textContent = failureExplanation(field);

  const comparison = document.createElement("dl");
  comparison.className = "value-comparison";
  comparison.append(
    createValuePair("Application says", field.expected || "Not entered"),
    createValuePair("Label says", field.actual || "Not found"),
  );

  card.append(header, explanation, comparison);
  return card;
}

function createPassRow(field) {
  const row = document.createElement("div");
  row.className = "result-card result-pass";
  const name = document.createElement("span");
  name.className = "pass-name";
  name.textContent = displayFieldName(field.field_name);
  row.append(name, createStatusBadge("PASS"));
  return row;
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status.toLowerCase()}`;
  badge.textContent = status;
  return badge;
}

function createValuePair(label, value) {
  const group = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value;
  group.append(term, description);
  return group;
}

function displayFieldName(fieldName) {
  return FIELD_LABELS[fieldName] || "Label Information";
}

function failureExplanation(field) {
  if (!field.actual.trim()) {
    return "We could not read this information on the label.";
  }
  if (field.field_name === "government_warning") {
    return "The warning does not match exactly. Check every capital letter, punctuation mark, and space.";
  }
  return "The application and label do not match.";
}

function resetPage() {
  releasePreviewUrl();
  form.reset();
  clearErrors();
  selectedFile.textContent = "No photo selected.";
  imagePreview.hidden = true;
  imagePreview.removeAttribute("src");
  document.getElementById("failed-results").replaceChildren();
  document.getElementById("passed-results").replaceChildren();
  resultsView.hidden = true;
  formView.hidden = false;
  window.scrollTo({ top: 0, behavior: "auto" });
  document.getElementById("page-title").focus();
}
