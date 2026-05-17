from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    EmployeeProfile,
    Review,
    ScheduleException,
    Service,
    ServiceCategory,
    User,
    UserRole,
    WeekDay,
)


class AppointmentForm(forms.Form):
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Электронная почта")
    service = forms.ModelChoiceField(
        label="Услуга",
        queryset=Service.objects.filter(is_active=True, category__is_active=True),
        empty_label="Выберите услугу",
    )
    employee = forms.ModelChoiceField(
        label="Специалист",
        queryset=EmployeeProfile.objects.none(),
        empty_label="Выберите специалиста",
    )
    date = forms.DateField(
        label="Дата",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    time = forms.CharField(
        label="Время",
        widget=forms.Select(),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        self.uses_authenticated_account = bool(
            user
            and user.is_authenticated
            and getattr(user, "role", "") in [UserRole.CLIENT, UserRole.EMPLOYEE]
        )
        self.is_authenticated_client = self.uses_authenticated_account

        if args and self.uses_authenticated_account:
            mutable_data = args[0].copy()
            mutable_data["last_name"] = user.last_name
            mutable_data["first_name"] = user.first_name
            mutable_data["middle_name"] = user.middle_name
            mutable_data["phone_number"] = user.phone_number
            mutable_data["email"] = user.email
            args = (mutable_data,) + args[1:]

        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

        self.fields["time"].widget.choices = [
            ("", "Сначала выберите услугу, специалиста и дату")
        ]

        selected_service = None
        service_value = self.data.get("service") or self.initial.get("service")
        if service_value:
            try:
                selected_service = Service.objects.get(
                    pk=service_value,
                    is_active=True,
                    category__is_active=True,
                )
            except (Service.DoesNotExist, TypeError, ValueError):
                selected_service = None

        if selected_service is not None:
            self.fields["employee"].queryset = (
                selected_service.employees.select_related("user")
                .filter(is_active=True)
                .order_by("user__last_name", "user__first_name", "user__middle_name")
            )

        selected_employee = None
        employee_value = self.data.get("employee") or self.initial.get("employee")
        if employee_value and selected_service is not None:
            try:
                selected_employee = self.fields["employee"].queryset.get(pk=employee_value)
            except (EmployeeProfile.DoesNotExist, TypeError, ValueError):
                selected_employee = None

        selected_date = self.data.get("date") or self.initial.get("date")
        if selected_service is not None and selected_employee is not None and selected_date:
            try:
                if isinstance(selected_date, str):
                    selected_date = forms.DateField().clean(selected_date)

                from .services.booking_service import get_availability_details

                availability = get_availability_details(
                    selected_employee,
                    selected_service,
                    selected_date,
                )
                time_choices = [
                    (slot.strftime("%H:%M"), slot.strftime("%H:%M"))
                    for slot in availability.slots
                ]
                if time_choices:
                    self.fields["time"].widget.choices = time_choices
                else:
                    self.fields["time"].widget.choices = [
                        ("", "Нет свободных слотов для записи")
                    ]
            except forms.ValidationError:
                pass

        if self.uses_authenticated_account:
            self.fields["last_name"].initial = user.last_name
            self.fields["first_name"].initial = user.first_name
            self.fields["middle_name"].initial = user.middle_name
            self.fields["phone_number"].initial = user.phone_number
            self.fields["email"].initial = user.email

            for field_name in [
                "last_name",
                "first_name",
                "middle_name",
                "phone_number",
                "email",
            ]:
                self.fields[field_name].required = False
                self.fields[field_name].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()

        if self.uses_authenticated_account:
            cleaned_data["last_name"] = self.user.last_name
            cleaned_data["first_name"] = self.user.first_name
            cleaned_data["middle_name"] = self.user.middle_name
            cleaned_data["phone_number"] = self.user.phone_number
            cleaned_data["email"] = self.user.email

        return cleaned_data


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = [
            "name",
            "category",
            "description",
            "price",
            "duration_minutes",
            "employees",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "employees": forms.CheckboxSelectMultiple(),
        }
        labels = {
            "name": "Название",
            "category": "Категория",
            "description": "Описание",
            "price": "Цена",
            "duration_minutes": "Длительность (мин)",
            "employees": "Специалисты",
            "is_active": "Активна",
        }
        help_texts = {
            "employees": "Отметьте сотрудников, которые оказывают эту услугу.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_employee_ids = []
        if self.instance.pk:
            selected_employee_ids = list(self.instance.employees.values_list("pk", flat=True))

        employee_queryset = (
            EmployeeProfile.objects.select_related("user")
            .filter(Q(is_active=True) | Q(pk__in=selected_employee_ids))
            .order_by("user__last_name", "user__first_name", "user__middle_name", "position")
            .distinct()
        )
        self.fields["employees"].queryset = employee_queryset
        self.fields["category"].queryset = ServiceCategory.objects.order_by("name")

        self.fields["name"].widget.attrs.update({"class": "form-control"})
        self.fields["category"].widget.attrs.update({"class": "form-select"})
        self.fields["description"].widget.attrs.update({"class": "form-control"})
        self.fields["price"].widget.attrs.update(
            {"class": "form-control", "step": "0.01", "min": "0"}
        )
        self.fields["duration_minutes"].widget.attrs.update(
            {"class": "form-control", "min": "5"}
        )
        self.fields["employees"].widget.attrs.update({"class": "list-unstyled mb-0"})
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})

        self.fields["employees"].label_from_instance = (
            lambda employee: f"{employee.user} ({employee.position})"
        )


class ServiceCategoryForm(forms.ModelForm):
    class Meta:
        model = ServiceCategory
        fields = ["name", "description", "image", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "name": "Название",
            "description": "Описание",
            "image": "Изображение",
            "is_active": "Активна",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"class": "form-control"})
        self.fields["description"].widget.attrs.update({"class": "form-control"})
        self.fields["image"].widget.attrs.update({"class": "form-control"})
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})


class EmployeeCreateForm(forms.Form):
    username = forms.CharField(label="Логин", max_length=150)
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Email")
    position = forms.CharField(label="Должность", max_length=100)
    description = forms.CharField(
        label="Описание",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    experience_years = forms.IntegerField(label="Опыт (лет)", min_value=0)
    hire_date = forms.DateField(
        label="Дата приема на работу",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    work_days = forms.MultipleChoiceField(
        label="Рабочие дни",
        required=False,
        choices=WeekDay.choices,
        widget=forms.CheckboxSelectMultiple(),
    )
    work_start_time = forms.TimeField(
        label="Начало рабочего дня",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    work_end_time = forms.TimeField(
        label="Конец рабочего дня",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    photo = forms.ImageField(label="Фото", required=False)
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop("user_instance", None)
        super().__init__(*args, **kwargs)

        field_classes = {
            "username": "form-control",
            "last_name": "form-control",
            "first_name": "form-control",
            "middle_name": "form-control",
            "phone_number": "form-control",
            "email": "form-control",
            "position": "form-control",
            "description": "form-control",
            "experience_years": "form-control",
            "hire_date": "form-control",
            "work_start_time": "form-control",
            "work_end_time": "form-control",
            "photo": "form-control",
            "is_active": "form-check-input",
        }
        for field_name, css_class in field_classes.items():
            self.fields[field_name].widget.attrs.update({"class": css_class})

        self.fields["work_days"].widget.attrs.update({"class": "list-unstyled mb-0"})

    def _user_queryset(self):
        queryset = User.objects.all()
        if self.user_instance is not None:
            queryset = queryset.exclude(pk=self.user_instance.pk)
        return queryset

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not username:
            raise forms.ValidationError("Укажите логин сотрудника.")

        duplicate_user = self._user_queryset().filter(
            Q(username__iexact=username)
            | Q(email__iexact=username)
            | Q(phone_number=username)
        )
        if duplicate_user.exists():
            raise forms.ValidationError(
                "Такой логин уже занят или совпадает с телефоном/email другого пользователя."
            )
        return username

    def clean_phone_number(self):
        phone_number = self.cleaned_data["phone_number"].strip()
        if self._user_queryset().filter(phone_number=phone_number).exists():
            raise forms.ValidationError("Пользователь с таким телефоном уже существует.")
        return phone_number

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self._user_queryset().filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        work_days = cleaned_data.get("work_days") or []
        work_start_time = cleaned_data.get("work_start_time")
        work_end_time = cleaned_data.get("work_end_time")

        if bool(work_start_time) != bool(work_end_time):
            raise forms.ValidationError(
                "Для графика нужно указать и начало, и конец рабочего дня."
            )

        if work_start_time and work_end_time and work_start_time >= work_end_time:
            self.add_error(
                "work_end_time",
                "Время окончания должно быть позже времени начала.",
            )

        if work_days and not (work_start_time and work_end_time):
            raise forms.ValidationError(
                "Если выбраны рабочие дни, укажите время работы."
            )

        if (work_start_time or work_end_time) and not work_days:
            raise forms.ValidationError(
                "Если указано время работы, выберите хотя бы один рабочий день."
            )

        try:
            cleaned_data["work_days"] = sorted({int(day) for day in work_days})
        except (TypeError, ValueError):
            raise forms.ValidationError("В рабочих днях найдено некорректное значение.")
        return cleaned_data


class ScheduleExceptionForm(forms.ModelForm):
    class Meta:
        model = ScheduleException
        fields = [
            "employee",
            "exception_type",
            "start_datetime",
            "end_datetime",
            "reason",
        ]
        labels = {
            "employee": "Сотрудник",
            "exception_type": "Тип исключения",
            "start_datetime": "Начало",
            "end_datetime": "Окончание",
            "reason": "Комментарий",
        }
        widgets = {
            "start_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "reason": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_employee_ids = []
        if self.instance.pk:
            selected_employee_ids = [self.instance.employee_id]

        employee_queryset = (
            EmployeeProfile.objects.select_related("user")
            .filter(Q(is_active=True) | Q(pk__in=selected_employee_ids))
            .order_by("user__last_name", "user__first_name", "user__middle_name", "position")
            .distinct()
        )
        self.fields["employee"].queryset = employee_queryset
        self.fields["employee"].label_from_instance = (
            lambda employee: f"{employee.user} ({employee.position})"
        )

        self.fields["employee"].widget.attrs.update({"class": "form-select"})
        self.fields["exception_type"].widget.attrs.update({"class": "form-select"})
        self.fields["start_datetime"].widget.attrs.update({"class": "form-control"})
        self.fields["end_datetime"].widget.attrs.update({"class": "form-control"})
        self.fields["reason"].widget.attrs.update({"class": "form-control"})

        self.fields["start_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["end_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]

        if self.instance.pk:
            if self.instance.start_datetime:
                self.initial["start_datetime"] = timezone.localtime(
                    self.instance.start_datetime
                ).strftime("%Y-%m-%dT%H:%M")
            if self.instance.end_datetime:
                self.initial["end_datetime"] = timezone.localtime(
                    self.instance.end_datetime
                ).strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned_data = super().clean()
        start_datetime = cleaned_data.get("start_datetime")
        end_datetime = cleaned_data.get("end_datetime")

        if start_datetime and timezone.is_naive(start_datetime):
            cleaned_data["start_datetime"] = timezone.make_aware(start_datetime)

        if end_datetime and timezone.is_naive(end_datetime):
            cleaned_data["end_datetime"] = timezone.make_aware(end_datetime)

        if cleaned_data.get("start_datetime") and cleaned_data.get("end_datetime"):
            if cleaned_data["start_datetime"] >= cleaned_data["end_datetime"]:
                self.add_error("end_datetime", "Окончание должно быть позже начала.")

        return cleaned_data


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "text"]
        labels = {
            "rating": "Оценка",
            "text": "Отзыв",
        }
        widgets = {
            "rating": forms.Select(
                choices=[
                    (5, "5 - отлично"),
                    (4, "4 - хорошо"),
                    (3, "3 - нормально"),
                    (2, "2 - плохо"),
                    (1, "1 - очень плохо"),
                ]
            ),
            "text": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rating"].widget.attrs.update({"class": "form-select"})
        self.fields["text"].widget.attrs.update({"class": "form-control"})


class LoginForm(forms.Form):
    identifier = forms.CharField(label="Телефон, email или логин", max_length=150)
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})


class RegistrationForm(forms.Form):
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Email")
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput())
    password2 = forms.CharField(label="Повторите пароль", widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

    def clean_phone_number(self):
        phone_number = self.cleaned_data["phone_number"].strip()
        if User.objects.filter(phone_number=phone_number).exists():
            raise forms.ValidationError("Пользователь с таким телефоном уже существует.")
        return phone_number

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Пароли не совпадают.")

        if password1 and len(password1) < 8:
            self.add_error("password1", "Пароль должен содержать минимум 8 символов.")

        return cleaned_data
