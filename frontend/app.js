const statusElement = document.getElementById("health-status");

async function checkHealth() {
  try {
    const response = await fetch("/health", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Health check failed with status ${response.status}`);
    }

    const data = await response.json();

    if (data.status !== "healthy") {
      throw new Error("Health check returned an unexpected response.");
    }

    statusElement.textContent = `Health response: ${JSON.stringify(data)}`;
    statusElement.className = "status status-ok";
  } catch {
    statusElement.textContent = "The app is not connected. Please try again shortly.";
    statusElement.className = "status status-error";
  }
}

checkHealth();
