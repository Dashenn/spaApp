document.addEventListener("DOMContentLoaded", function () {
  if (document.querySelector(".bannerSwiper")) {
    new Swiper(".bannerSwiper", {
      loop: true,
      autoplay: {
        delay: 5000,
        disableOnInteraction: false,
      },
      pagination: {
        el: ".swiper-pagination",
        clickable: true,
      },
      effect: "fade",
      fadeEffect: {
        crossFade: true,
      },
    });
  }

  document.querySelectorAll("[data-auto-dismiss]").forEach((message) => {
    const delay = Number(message.dataset.autoDismiss || 5000);

    window.setTimeout(() => {
      if (!document.body.contains(message)) {
        return;
      }

      if (window.bootstrap && window.bootstrap.Alert) {
        window.bootstrap.Alert.getOrCreateInstance(message).close();
        return;
      }

      message.classList.remove("show");
      window.setTimeout(() => message.remove(), 250);
    }, delay);
  });

  const bookingForm = document.querySelector("[data-booking-form]");
  if (!bookingForm) {
    return;
  }

  const serviceSelect = bookingForm.querySelector("#id_service");
  const employeeSelect = bookingForm.querySelector("#id_employee");
  const dateInput = bookingForm.querySelector("#id_date");
  const timeSelect = bookingForm.querySelector("#id_time");
  const durationInfo = bookingForm.querySelector("[data-duration-info]");
  const employeesUrl = bookingForm.dataset.employeesUrl || "/get-service-employees/";
  const timesUrl = bookingForm.dataset.timesUrl || "/get-available-times/";
  const employeeCatalogElement = document.getElementById("booking-employees-data");
  const initialEmployeeValue = employeeSelect.value;
  const initialTimeValue = timeSelect.value;
  let employeeCatalog = [];

  if (!serviceSelect || !employeeSelect || !dateInput || !timeSelect) {
    return;
  }

  if (employeeCatalogElement) {
    try {
      employeeCatalog = JSON.parse(employeeCatalogElement.textContent);
    } catch (error) {
      employeeCatalog = [];
    }
  }

  function clearSelect(select, text) {
    select.innerHTML = "";
    const option = document.createElement("option");
    option.textContent = text;
    option.value = "";
    option.selected = true;
    select.appendChild(option);
  }

  function clearTimeSelect(text) {
    clearSelect(timeSelect, text || "Сначала выберите услугу, специалиста и дату");
  }

  function getEmployeesForService(serviceId) {
    const numericServiceId = Number(serviceId);
    if (!numericServiceId || !employeeCatalog.length) {
      return [];
    }

    return employeeCatalog.filter((employee) =>
      Array.isArray(employee.service_ids) && employee.service_ids.includes(numericServiceId)
    );
  }

  function renderEmployees(employees, preferredEmployeeId = "", preferredTimeValue = "") {
    clearSelect(employeeSelect, "Выберите специалиста");

    if (!employees || employees.length === 0) {
      clearSelect(employeeSelect, "Для услуги нет назначенных специалистов");
      clearTimeSelect("Сначала выберите специалиста и дату");
      return;
    }

    employees.forEach((employee) => {
      const option = document.createElement("option");
      option.value = employee.id;
      option.textContent = employee.name;
      employeeSelect.appendChild(option);
    });

    if (preferredEmployeeId) {
      const matchedOption = Array.from(employeeSelect.options).find(
        (option) => option.value === preferredEmployeeId
      );
      if (matchedOption) {
        employeeSelect.value = preferredEmployeeId;
      }
    }

    if (employeeSelect.value && dateInput.value) {
      updateAvailableTimes(preferredTimeValue);
    } else {
      clearTimeSelect("Сначала выберите специалиста и дату");
    }
  }

  function updateEmployees(preferredEmployeeId = "", preferredTimeValue = "") {
    const serviceId = serviceSelect.value;

    clearSelect(employeeSelect, "Сначала выберите услугу");
    clearTimeSelect();
    durationInfo.textContent = "";

    if (!serviceId) return;

    const localEmployees = getEmployeesForService(serviceId);
    if (localEmployees.length > 0) {
      renderEmployees(localEmployees, preferredEmployeeId, preferredTimeValue);
      return;
    }

    fetch(`${employeesUrl}?service=${serviceId}`)
      .then((response) => response.json())
      .then((data) => {
        renderEmployees(data.employees || [], preferredEmployeeId, preferredTimeValue);
      })
      .catch(() => {
        clearSelect(employeeSelect, "Не удалось загрузить специалистов");
        clearTimeSelect("Попробуйте выбрать услугу еще раз");
      });
  }

  function updateAvailableTimes(preferredTimeValue = "") {
    const serviceId = serviceSelect.value;
    const employeeId = employeeSelect.value;
    const selectedDate = dateInput.value;

    if (!serviceId || !employeeId || !selectedDate) {
      clearTimeSelect("Сначала выберите услугу, специалиста и дату");
      durationInfo.textContent = "";
      return;
    }

    fetch(`${timesUrl}?service=${serviceId}&employee=${employeeId}&date=${selectedDate}`)
      .then((response) => response.json())
      .then((data) => {
        timeSelect.innerHTML = "";

        if (!data.times || data.times.length === 0) {
          const option = document.createElement("option");
          option.textContent = "Нет свободных слотов для записи";
          option.value = "";
          option.disabled = true;
          option.selected = true;
          timeSelect.appendChild(option);
        } else {
          data.times.forEach((time) => {
            const option = document.createElement("option");
            option.value = time;
            option.textContent = time;
            timeSelect.appendChild(option);
          });

          if (preferredTimeValue) {
            const matchedOption = Array.from(timeSelect.options).find(
              (option) => option.value === preferredTimeValue
            );
            if (matchedOption) {
              timeSelect.value = preferredTimeValue;
            }
          }
        }

        if (data.duration) {
          const hours = Math.floor(data.duration / 60);
          const minutes = data.duration % 60;
          let text = "Длительность: ";
          if (hours > 0) text += `${hours} ч `;
          if (minutes > 0) text += `${minutes} мин`;
          durationInfo.textContent = text;
        } else {
          durationInfo.textContent = "";
        }
      })
      .catch(() => {
        clearTimeSelect("Не удалось загрузить время");
        durationInfo.textContent = "";
      });
  }

  serviceSelect.addEventListener("change", function () {
    updateEmployees();
  });
  employeeSelect.addEventListener("change", function () {
    updateAvailableTimes();
  });
  dateInput.addEventListener("change", function () {
    updateAvailableTimes();
  });

  clearSelect(employeeSelect, serviceSelect.value ? "Выберите специалиста" : "Сначала выберите услугу");
  clearTimeSelect();

  if (serviceSelect.value) {
    updateEmployees(initialEmployeeValue, initialTimeValue);
  }
});
