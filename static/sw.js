self.addEventListener("push", event => {
  const data = event.data.json();

  self.registration.showNotification(data.title, {
    body: data.body,
    icon: "/icon.png",
    vibrate: [200, 100, 200],
    tag: "tennis-alert"
  });
});
