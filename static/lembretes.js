// MEDTIME - scripts simples da interface (sem bibliotecas externas)

// 1) Confirmação antes de excluir
document.querySelectorAll("form[data-confirmar]").forEach(function (form) {
  form.addEventListener("submit", function (e) {
    if (!confirm(form.dataset.confirmar)) e.preventDefault();
  });
});

// 2) Formulário: adicionar e remover horários
(function () {
  var lista = document.getElementById("horarios");
  var botao = document.getElementById("add-horario");
  if (!lista || !botao) return;

  function novaLinha(valor) {
    var linha = document.createElement("div");
    linha.className = "linha-horario";
    linha.innerHTML = '<input type="time" name="horario" aria-label="Horário">' +
      '<button type="button" class="btn-link remover-horario">Remover</button>';
    linha.querySelector("input").value = valor || "";
    lista.appendChild(linha);
    return linha;
  }
  botao.addEventListener("click", function () { novaLinha("").querySelector("input").focus(); });
  lista.addEventListener("click", function (e) {
    if (!e.target.classList.contains("remover-horario")) return;
    if (lista.children.length > 1) e.target.parentElement.remove();
    else lista.querySelector("input").value = "";
  });
  if (!lista.children.length) novaLinha("08:00");
})();

// 3) Painel: destaque de doses atrasadas e aviso do navegador (só com a página aberta)
(function () {
  var doses = document.querySelectorAll(".dose");
  if (!doses.length) return;

  var caixa = document.getElementById("avisos-navegador");
  var ativar = document.getElementById("ativar-avisos");
  var suportado = "Notification" in window;
  if (caixa && suportado && Notification.permission === "default") caixa.hidden = false;
  if (ativar) {
    ativar.addEventListener("click", function () {
      Notification.requestPermission().then(function () { caixa.hidden = true; });
    });
  }

  function minutosAgora() {
    var d = new Date();
    return d.getHours() * 60 + d.getMinutes();
  }

  function verificar() {
    var agora = minutosAgora();
    doses.forEach(function (el) {
      if (el.dataset.status !== "pendente") return;
      var partes = el.dataset.hora.split(":");
      var alvo = parseInt(partes[0], 10) * 60 + parseInt(partes[1], 10);
      if (agora < alvo) return;

      el.classList.add("atrasada");
      el.querySelector(".atraso").hidden = false;

      var chave = "medtime-aviso-" + new Date().toDateString() + "-" + el.dataset.nome + "-" + el.dataset.hora;
      if (suportado && Notification.permission === "granted" && !sessionStorage.getItem(chave)) {
        sessionStorage.setItem(chave, "1");
        new Notification("Hora do remédio", { body: el.dataset.nome + " – " + el.dataset.hora });
      }
    });
  }
  verificar();
  setInterval(verificar, 30000);
})();
