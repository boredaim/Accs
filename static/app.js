const accountsList = document.getElementById("accounts");

if (accountsList) {
    const reorderUrl = accountsList.dataset.reorderUrl;
    Sortable.create(accountsList, {
        animation: 150,
        handle: ".card",
        onEnd: () => {
            const order = Array.from(accountsList.querySelectorAll(".card")).map(
                (card) => Number(card.dataset.id)
            );
            fetch(reorderUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ order }),
            }).catch(() => {
                // Non-blocking; UI order stays visible even if request fails.
            });
        },
    });
}
