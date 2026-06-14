from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, DecimalField, Max, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

from .forms import (
    AppointmentForm,
    EmployeeCreateForm,
    LoginForm,
    RegistrationForm,
    ReviewForm,
    ScheduleExceptionForm,
    ServiceCategoryForm,
    ServiceForm,
)
from .models import (
    Appointment,
    AppointmentStatus,
    ClientProfile,
    EmployeeProfile,
    PaymentMethod,
    Review,
    ScheduleException,
    Service,
    ServiceCategory,
    User,
    UserRole,
)
from .services.booking_service import get_availability_details
from .utils import (
    build_client_password_setup_url,
    send_appointment_email,
    send_employee_invitation_email,
)

def _is_admin_user(user):
    return user.is_authenticated and getattr(user, "role", "") == UserRole.ADMIN

def _is_employee_user(user):
    return user.is_authenticated and getattr(user, "role", "") == UserRole.EMPLOYEE

admin_required = user_passes_test(_is_admin_user, login_url="login")
employee_required = user_passes_test(_is_employee_user, login_url="login")

def _build_user_full_name(user):
    parts = [user.last_name, user.first_name, user.middle_name]
    full_name = " ".join(part.strip() for part in parts if part and part.strip())
    return full_name or user.username

def _send_appointment_email_safely(
    request,
    appointment,
    subject,
    template_name,
    warning_message,
    account_setup_url=None,
):
    try:
        send_appointment_email(
            appointment,
            subject,
            template_name,
            account_setup_url=account_setup_url,
        )
    except Exception:
        messages.warning(request, warning_message)


def _can_review_appointment(appointment):
    return (
        appointment.end_datetime < timezone.now()
        and appointment.status == AppointmentStatus.COMPLETED
    )

def _decimal_sum():
    return Coalesce(
        Sum("amount"),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=10, decimal_places=2),
    )

def _month_start_bounds(target_date):
    month_start_date = target_date.replace(day=1)
    if month_start_date.month == 12:
        next_month_date = date(month_start_date.year + 1, 1, 1)
    else:
        next_month_date = date(month_start_date.year, month_start_date.month + 1, 1)

    month_start = timezone.make_aware(
        datetime.combine(month_start_date, datetime.min.time())
    )
    next_month_start = timezone.make_aware(
        datetime.combine(next_month_date, datetime.min.time())
    )
    return month_start_date, month_start, next_month_start

def _shift_month(target_date, delta_months):
    month_index = target_date.year * 12 + (target_date.month - 1) + delta_months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)

def _month_label(target_date):
    month_names = [
        "Янв",
        "Фев",
        "Мар",
        "Апр",
        "Май",
        "Июн",
        "Июл",
        "Авг",
        "Сен",
        "Окт",
        "Ноя",
        "Дек",
    ]
    return f"{month_names[target_date.month - 1]} {target_date.year}"

def _weekday_label(weekday_index):
    labels = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]
    return labels[weekday_index]

def _add_validation_error_to_form(form, error):
    if hasattr(error, "message_dict"):
        for field_name, messages_list in error.message_dict.items():
            if field_name in form.fields:
                for message in messages_list:
                    form.add_error(field_name, message)
            else:
                for message in messages_list:
                    form.add_error(None, message)
        return

    for message in error.messages:
        form.add_error(None, message)

def _build_employee_form_initial(employee):
    return {
        "username": employee.user.username,
        "last_name": employee.user.last_name,
        "first_name": employee.user.first_name,
        "middle_name": employee.user.middle_name,
        "phone_number": employee.user.phone_number,
        "email": employee.user.email,
        "position": employee.position,
        "description": employee.description,
        "experience_years": employee.experience_years,
        "hire_date": employee.hire_date,
        "work_days": [str(day) for day in employee.work_days],
        "work_start_time": employee.work_start_time,
        "work_end_time": employee.work_end_time,
        "is_active": employee.is_active,
    }

def _build_booking_employee_catalog():
    employees = (
        EmployeeProfile.objects.select_related("user")
        .prefetch_related("services")
        .filter(
            is_active=True,
            services__is_active=True,
            services__category__is_active=True,
        )
        .distinct()
        .order_by("user__last_name", "user__first_name", "user__middle_name")
    )

    catalog = []
    for employee in employees:
        service_ids = list(
            employee.services.filter(
                is_active=True, category__is_active=True
            ).values_list(
                "id",
                flat=True,
            )
        )
        if not service_ids:
            continue

        catalog.append(
            {
                "id": employee.id,
                "name": str(employee.user),
                "service_ids": service_ids,
            }
        )

    return catalog

def _build_exception_status(exception):
    now = timezone.now()
    if exception.end_datetime < now:
        return "Завершено", "secondary"
    if exception.start_datetime <= now <= exception.end_datetime:
        return "Идет сейчас", "danger"
    return "Запланировано", "warning"

def _save_employee_from_form(form, employee=None):
    username = form.cleaned_data["username"]
    phone = form.cleaned_data["phone_number"]
    user = employee.user if employee else None

    try:
        with transaction.atomic():
            if user is None:
                user = User.objects.create_user(
                    username=username,
                    password=None,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    middle_name=form.cleaned_data["middle_name"],
                    email=form.cleaned_data["email"],
                    phone_number=phone,
                    role=UserRole.EMPLOYEE,
                    is_staff=True,
                )

            user.username = username
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.middle_name = form.cleaned_data["middle_name"]
            user.email = form.cleaned_data["email"]
            user.phone_number = phone
            user.role = UserRole.EMPLOYEE
            user.is_staff = True
            user.save()

            if employee is None:
                employee = EmployeeProfile(user=user)

            employee.position = form.cleaned_data["position"]
            employee.description = form.cleaned_data["description"]
            employee.experience_years = form.cleaned_data["experience_years"]
            employee.hire_date = form.cleaned_data["hire_date"]
            employee.work_days = form.cleaned_data["work_days"]
            employee.work_start_time = form.cleaned_data["work_start_time"]
            employee.work_end_time = form.cleaned_data["work_end_time"]
            employee.is_active = form.cleaned_data.get("is_active", False)

            if form.cleaned_data.get("photo"):
                employee.photo = form.cleaned_data["photo"]

            employee.full_clean()
            employee.save()
    except ValidationError as error:
        _add_validation_error_to_form(form, error)
        return None

    return employee

def _save_exception_from_form(form, exception=None):
    try:
        with transaction.atomic():
            if exception is None:
                exception = ScheduleException()

            exception.employee = form.cleaned_data["employee"]
            exception.exception_type = form.cleaned_data["exception_type"]
            exception.start_datetime = form.cleaned_data["start_datetime"]
            exception.end_datetime = form.cleaned_data["end_datetime"]
            exception.reason = form.cleaned_data["reason"]
            exception.full_clean()
            exception.save()
    except ValidationError as error:
        _add_validation_error_to_form(form, error)
        return None

    return exception

def _create_booking_from_form(request, form):
    service = form.cleaned_data["service"]
    employee = form.cleaned_data["employee"]
    selected_date = form.cleaned_data["date"]
    selected_time = form.cleaned_data["time"]

    start_datetime = timezone.make_aware(
        datetime.combine(
            selected_date,
            datetime.strptime(selected_time, "%H:%M").time(),
        )
    )
    end_datetime = start_datetime + timedelta(minutes=service.duration_minutes)

    availability = get_availability_details(employee, service, selected_date)
    available_times = [slot.strftime("%H:%M") for slot in availability.slots]

    if selected_time not in available_times:
        form.add_error(
            "time",
            availability.message or "Выбранное время уже недоступно.",
        )
        return False

    new_client_needs_password = False

    with transaction.atomic():
        if form.uses_authenticated_account:
            user = request.user
            client_profile, _ = ClientProfile.objects.get_or_create(user=user)
        else:
            phone = form.cleaned_data["phone_number"]
            email = form.cleaned_data["email"]

            if User.objects.filter(phone_number=phone).exists():
                form.add_error(
                    "phone_number",
                    "Пользователь с таким телефоном уже существует. "
                    "Войдите в личный кабинет, чтобы создать запись.",
                )
                return False

            if User.objects.filter(email__iexact=email).exists():
                form.add_error(
                    "email",
                    "Пользователь с такой почтой уже существует. "
                    "Войдите в личный кабинет, чтобы создать запись.",
                )
                return False

            user = User.objects.create_user(
                username=phone,
                password=None,
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                middle_name=form.cleaned_data["middle_name"],
                email=email,
                phone_number=phone,
                role=UserRole.CLIENT,
            )

            new_client_needs_password = True
            client_profile = ClientProfile.objects.create(user=user)

        appointment = Appointment(
            client=client_profile,
            employee=employee,
            service=service,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            status=AppointmentStatus.CREATED,
        )
        appointment.full_clean()
        appointment.save()

    account_setup_url = None

    if new_client_needs_password:
        account_setup_url = build_client_password_setup_url(
            request,
            appointment.client.user,
        )

    _send_appointment_email_safely(
        request,
        appointment,
        "Вы записались в Lotus Bloom",
        "spa_app/email/appointment_created.html",
        "Письмо не доставлено.",
        account_setup_url=account_setup_url,
    )

    messages.success(request, "Запись успешно создана.")
    return True

def _build_booking_form(request):
    if request.method == "POST" and request.POST.get("form_type") == "booking":
        form = AppointmentForm(request.POST, user=request.user)
        if form.is_valid():
            if _create_booking_from_form(request, form):
                return form, redirect(f"{request.path}#booking-form")
            messages.error(
                request,
                "Не удалось создать запись. Проверьте выбранные дату и время.",
            )
        else:
            messages.error(request, "Проверьте данные в форме записи.")
        return form, None

    return AppointmentForm(user=request.user), None

def _render_public_page(request, template_name, context):
    booking_form, redirect_response = _build_booking_form(request)
    if redirect_response is not None:
        return redirect_response

    context.update(
        {
            "booking_form": booking_form,
            # "booking_is_authenticated_client": booking_form.uses_authenticated_account,
            "booking_uses_account": booking_form.uses_authenticated_account,
            "booking_employee_catalog": _build_booking_employee_catalog(),
        }
    )
    return render(request, template_name, context)

def index(request):
    categories = ServiceCategory.objects.filter(is_active=True)
    specialists = EmployeeProfile.objects.select_related("user").filter(is_active=True)
    reviews = Review.objects.select_related("client__user").filter(is_published=True)[
        :5
    ]

    for review in reviews:
        review.stars_filled = range(review.rating)
        review.stars_empty = range(5 - review.rating)

    return _render_public_page(
        request,
        "spa_app/pages/main.html",
        {
            "categories": categories,
            "specialists": specialists,
            "reviews": reviews,
            "active_page": "index",
        },
    )

def about(request):
    specialists = EmployeeProfile.objects.filter(is_active=True)

    return _render_public_page(
        request,
        "spa_app/pages/about.html",
        {
            "specialists": specialists,
            "active_page": "about",
        },
    )

def services(request):
    categories = ServiceCategory.objects.prefetch_related(
        Prefetch(
            "services",
            queryset=Service.objects.filter(is_active=True).order_by("name"),
        )
    ).filter(is_active=True)

    return _render_public_page(
        request,
        "spa_app/pages/services.html",
        {
            "categories": categories,
            "active_page": "services",
        },
    )

def contact(request):
    return render(
        request,
        "spa_app/pages/contact.html",
        {"active_page": "contact"},
    )

def login_view(request):
    if request.user.is_authenticated:
        if request.user.role == UserRole.ADMIN:
            return redirect("dashboard_home")
        return redirect("index")

    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if next_url == "None":
        next_url = ""

    if request.method == "POST":
        form = LoginForm(request.POST)

        if form.is_valid():
            identifier = form.cleaned_data["identifier"].strip()
            password = form.cleaned_data["password"]

            user = (
                User.objects.filter(phone_number=identifier).first()
                or User.objects.filter(username=identifier).first()
            )

            if user is None:
                form.add_error("identifier", "Пользователь не найден.")
            elif not user.has_usable_password():
                form.add_error("password", "Пароль еще не создан.")
            else:
                auth_user = authenticate(
                    request,
                    username=user.username,
                    password=password,
                )

                if auth_user is None:
                    form.add_error("password", "Неверный пароль.")
                else:
                    login(request, auth_user)
                    messages.success(request, "Вы успешно вошли в аккаунт.")

                    if next_url:
                        return redirect(next_url)

                    if auth_user.role == UserRole.ADMIN:
                        return redirect("dashboard_home")

                    return redirect("index")

        messages.error(request, "Не удалось войти. Проверьте логин и пароль.")

    else:
        form = LoginForm()

    return render(
        request,
        "spa_app/pages/login.html",
        {
            "form": form,
            "next": next_url,
            "active_page": "login",
        },
    )

def register_view(request):
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data["phone_number"],
                    password=form.cleaned_data["password1"],
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    middle_name=form.cleaned_data["middle_name"],
                    email=form.cleaned_data["email"],
                    phone_number=form.cleaned_data["phone_number"],
                    role=UserRole.CLIENT,
                )
                ClientProfile.objects.create(user=user)

            login(request, user)
            messages.success(request, "Регистрация прошла успешно.")
            return redirect("index")
        messages.error(request, "Не удалось зарегистрироваться. Проверьте поля формы.")
    else:
        form = RegistrationForm()

    return render(
        request,
        "spa_app/pages/register.html",
        {
            "form": form,
            "active_page": "register",
        },
    )

def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "Вы вышли из аккаунта.")
    return redirect("index")

def employee_set_password(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        employee_user = User.objects.get(pk=uid, role=UserRole.EMPLOYEE)
    except (TypeError, ValueError, OverflowError, ValidationError, User.DoesNotExist):
        employee_user = None

    is_valid_link = employee_user is not None and default_token_generator.check_token(
        employee_user, token
    )

    if not is_valid_link:
        return render(
            request,
            "spa_app/pages/employee_set_password.html",
            {
                "active_page": "",
                "is_valid_link": False,
            },
        )

    if request.method == "POST":
        form = SetPasswordForm(employee_user, request.POST)

        if form.is_valid():
            form.save()
            messages.success(
                request, "Пароль создан. Теперь можно войти как сотрудник."
            )
            return redirect("login")

        messages.error(
            request, "Не удалось сохранить пароль. Проверьте требования к паролю."
        )
    else:
        form = SetPasswordForm(employee_user)

    return render(
        request,
        "spa_app/pages/employee_set_password.html",
        {
            "active_page": "",
            "employee_user": employee_user,
            "form": form,
            "is_valid_link": True,
        },
    )


def client_set_password(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        client_user = User.objects.get(pk=uid, role=UserRole.CLIENT)
    except (TypeError, ValueError, OverflowError, ValidationError, User.DoesNotExist):
        client_user = None

    is_valid_link = client_user is not None and default_token_generator.check_token(
        client_user, token
    )

    if not is_valid_link:
        return render(
            request,
            "spa_app/pages/client_set_password.html",
            {
                "active_page": "",
                "is_valid_link": False,
            },
        )

    if request.method == "POST":
        form = SetPasswordForm(client_user, request.POST)

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Пароль создан. Теперь вы можете войти в личный кабинет.",
            )
            return redirect("login")

        messages.error(
            request,
            "Не удалось сохранить пароль. Проверьте требования к паролю.",
        )
    else:
        form = SetPasswordForm(client_user)

    return render(
        request,
        "spa_app/pages/client_set_password.html",
        {
            "active_page": "",
            "client_user": client_user,
            "form": form,
            "is_valid_link": True,
        },
    )

@login_required
def account_view(request):
    client_profile, _ = ClientProfile.objects.get_or_create(user=request.user)
    appointments = list(
        Appointment.objects.select_related(
            "employee__user",
            "service",
            "review",
        )
        .filter(client=client_profile)
        .order_by("-start_datetime")
    )

    for appointment in appointments:
        appointment.start_local = timezone.localtime(appointment.start_datetime)
        appointment.end_local = timezone.localtime(appointment.end_datetime)
        appointment.can_review = _can_review_appointment(appointment)
        appointment.has_review = hasattr(appointment, "review")

    return render(
        request,
        "spa_app/pages/account.html",
        {
            "active_page": "account",
            "client_profile": client_profile,
            "full_name": _build_user_full_name(request.user),
            "appointments": appointments,
        },
    )

@login_required
def appointment_review(request, appointment_id):
    client_profile, _ = ClientProfile.objects.get_or_create(user=request.user)
    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "employee__user",
            "service",
            "client__user",
        ),
        pk=appointment_id,
        client=client_profile,
    )

    if not _can_review_appointment(appointment):
        messages.error(request, "Отзыв можно оставить только на прошедшую запись.")
        return redirect("account")

    review = getattr(appointment, "review", None)
    if request.method == "POST":
        form = ReviewForm(request.POST, instance=review)
        if form.is_valid():
            saved_review = form.save(commit=False)
            saved_review.appointment = appointment
            saved_review.client = client_profile
            saved_review.service = appointment.service
            saved_review.is_published = False
            saved_review.save()

            messages.success(request, "Отзыв отправлен на модерацию.")
            return redirect("account")
        messages.error(request, "Не удалось сохранить отзыв. Проверьте форму.")
    else:
        form = ReviewForm(instance=review)

    return render(
        request,
        "spa_app/pages/review_form.html",
        {
            "active_page": "account",
            "appointment": appointment,
            "form": form,
            "review": review,
        },
    )

@login_required
def client_confirm_appointment(request, pk):
    client_profile = get_object_or_404(ClientProfile, user=request.user)

    appointment = get_object_or_404(
        Appointment,
        pk=pk,
        client=client_profile,
    )

    if request.method == "POST":
        if appointment.status == AppointmentStatus.CREATED:
            appointment.status = AppointmentStatus.CONFIRMED
            appointment.save(update_fields=["status"])

            _send_appointment_email_safely(
                request,
                appointment,
                "Запись подтверждена",
                "spa_app/email/appointment_confirmed.html",
                "Письмо не доставлено.",
            )

            messages.success(request, "Запись подтверждена.")

    return redirect("account")

@login_required
def client_cancel_appointment(request, pk):
    client_profile = get_object_or_404(ClientProfile, user=request.user)

    appointment = get_object_or_404(
        Appointment,
        pk=pk,
        client=client_profile,
    )

    if request.method == "POST":
        if appointment.status in [
            AppointmentStatus.CREATED,
            AppointmentStatus.CONFIRMED,
        ]:
            appointment.status = AppointmentStatus.CANCELLED
            appointment.save(update_fields=["status"])

            _send_appointment_email_safely(
                request,
                appointment,
                "Запись отменена",
                "spa_app/email/appointment_cancelled.html",
                "Письмо не доставлено.",
            )

            messages.success(request, "Запись отменена.")

    return redirect("account")

@employee_required
def employee_portal(request):
    employee = get_object_or_404(
        EmployeeProfile.objects.select_related("user").prefetch_related("services"),
        user=request.user,
    )
    now = timezone.now()
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))

    appointments = list(
        Appointment.objects.select_related("client__user", "service")
        .filter(employee=employee)
        .order_by("-start_datetime")
    )
    today_appointments = []
    upcoming_appointments = []
    past_appointments = []

    for appointment in appointments:
        appointment.client_full_name = _build_user_full_name(appointment.client.user)
        appointment.start_local = timezone.localtime(appointment.start_datetime)
        appointment.end_local = timezone.localtime(appointment.end_datetime)

        if today_start <= appointment.start_datetime <= today_end:
            today_appointments.append(appointment)
        elif appointment.start_datetime > now:
            upcoming_appointments.append(appointment)
        else:
            past_appointments.append(appointment)

    upcoming_exceptions = list(
        ScheduleException.objects.filter(
            employee=employee, end_datetime__gte=now
        ).order_by("start_datetime")[:8]
    )
    for exception in upcoming_exceptions:
        exception.start_local = timezone.localtime(exception.start_datetime)
        exception.end_local = timezone.localtime(exception.end_datetime)

    return render(
        request,
        "spa_app/pages/employee_portal.html",
        {
            "active_page": "employee_portal",
            "employee": employee,
            "schedule_summary": employee.get_work_schedule_display(),
            "services": employee.services.filter(is_active=True).order_by("name"),
            "today_appointments": today_appointments,
            "upcoming_appointments": upcoming_appointments[:20],
            "past_appointments": past_appointments[:20],
            "upcoming_exceptions": upcoming_exceptions,
        },
    )

def get_service_employees(request):
    service_id = request.GET.get("service")

    if not service_id:
        return JsonResponse({"employees": []})

    try:
        service = Service.objects.get(
            id=service_id,
            is_active=True,
            category__is_active=True,
        )
        employees = (
            service.employees.select_related("user")
            .filter(is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
                "user__middle_name",
            )
        )

        data = [
            {"id": employee.id, "name": str(employee.user)} for employee in employees
        ]
        return JsonResponse({"employees": data})
    except Service.DoesNotExist:
        return JsonResponse({"employees": []})

def get_available_times(request):
    service_id = request.GET.get("service")
    employee_id = request.GET.get("employee")
    date_str = request.GET.get("date")

    if not service_id or not employee_id or not date_str:
        return JsonResponse({"times": []})

    try:
        service = Service.objects.get(
            id=service_id,
            is_active=True,
            category__is_active=True,
        )
        employee = EmployeeProfile.objects.get(id=employee_id, is_active=True)
        if not service.employees.filter(pk=employee.pk, is_active=True).exists():
            return JsonResponse(
                {
                    "times": [],
                    "duration": service.duration_minutes,
                    "message": "Этот специалист не оказывает выбранную услугу.",
                }
            )
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()

        availability = get_availability_details(employee, service, selected_date)

        return JsonResponse(
            {
                "times": [slot.strftime("%H:%M") for slot in availability.slots],
                "duration": service.duration_minutes,
                "message": availability.message,
                "reason": availability.reason,
            }
        )

    except (Service.DoesNotExist, EmployeeProfile.DoesNotExist, ValueError):
        return JsonResponse({"times": []}, status=400)

@admin_required
def dashboard_home(request):
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))

    context = {
        "today_appointments_count": Appointment.objects.filter(
            start_datetime__gte=today_start,
            start_datetime__lte=today_end,
        ).count(),
        "active_employees_count": EmployeeProfile.objects.filter(
            is_active=True
        ).count(),
        "services_count": Service.objects.count(),
        "active_page": "dashboard_home",
    }

    return render(request, "spa_app/dashboard/home.html", context)

@admin_required
def dashboard_appointments(request):
    appointments = Appointment.objects.select_related(
        "client__user",
        "employee__user",
        "service",
    ).all()

    employee_id = request.GET.get("employee")
    status = request.GET.get("status")

    if employee_id:
        appointments = appointments.filter(employee_id=employee_id)

    if status:
        appointments = appointments.filter(status=status)

    appointments = list(appointments.order_by("-start_datetime"))
    for appointment in appointments:
        appointment.client_full_name = _build_user_full_name(appointment.client.user)
        appointment.employee_full_name = _build_user_full_name(
            appointment.employee.user
        )

    employees = EmployeeProfile.objects.filter(is_active=True)

    return render(
        request,
        "spa_app/dashboard/appointments.html",
        {
            "appointments": appointments,
            "employees": employees,
            "selected_employee": employee_id or "",
            "selected_status": status or "",
            "active_page": "dashboard_appointments",
        },
    )

@admin_required
def confirm_appointment(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)

    if request.method == "POST" and appointment.status == AppointmentStatus.CREATED:
        appointment.status = AppointmentStatus.CONFIRMED
        appointment.save(update_fields=["status"])

        _send_appointment_email_safely(
            request,
            appointment,
            "Запись подтверждена",
            "spa_app/email/appointment_confirmed.html",
            "Письмо не доставлено.",
        )

        messages.success(request, "Запись подтверждена.")

    return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

@admin_required
def accept_appointment_payment(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)

    if request.method == "POST":
        payment_method = request.POST.get("payment_method")

        if payment_method not in [PaymentMethod.CASH, PaymentMethod.CARD]:
            messages.error(request, "Выберите способ оплаты.")
            return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

        appointment.is_paid = True
        appointment.is_refunded = False
        appointment.refunded_at = None
        appointment.payment_method = payment_method
        appointment.paid_at = timezone.now()
        appointment.save(
            update_fields=[
                "is_paid",
                "is_refunded",
                "refunded_at",
                "payment_method",
                "paid_at",
            ]
        )

        messages.success(request, "Оплата сохранена.")

    return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

@admin_required
def refund_appointment_payment(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)

    if request.method == "POST" and appointment.is_paid:
        appointment.is_refunded = True
        appointment.refunded_at = timezone.now()
        appointment.save(update_fields=["is_refunded", "refunded_at"])
        messages.success(request, "Возврат отмечен.")

    return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

@admin_required
def dashboard_clients(request):
    search_query = request.GET.get("q", "").strip()
    now = timezone.now()

    clients = (
        ClientProfile.objects.select_related("user")
        .annotate(
            appointments_count=Count("appointments", distinct=True),
            completed_appointments_count=Count(
                "appointments",
                filter=Q(appointments__status=AppointmentStatus.COMPLETED),
                distinct=True,
            ),
            last_visit_at=Max(
                "appointments__start_datetime",
                filter=Q(appointments__start_datetime__lte=now),
            ),
            paid_total=Coalesce(
                Sum(
                    "appointments__service__price",
                    filter=Q(appointments__is_paid=True),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        .filter(Q(user__role=UserRole.CLIENT) | Q(appointments_count__gt=0))
    )

    if search_query:
        clients = clients.filter(
            Q(user__last_name__icontains=search_query)
            | Q(user__first_name__icontains=search_query)
            | Q(user__middle_name__icontains=search_query)
            | Q(user__phone_number__icontains=search_query)
            | Q(user__email__icontains=search_query)
        )

    clients = list(
        clients.order_by("user__last_name", "user__first_name", "user__middle_name")
    )

    for client in clients:
        client.full_name = _build_user_full_name(client.user)
        client.phone = client.user.phone_number or "Не указано"
        client.email = client.user.email or "Не указано"
        client.last_visit_local = (
            timezone.localtime(client.last_visit_at) if client.last_visit_at else None
        )

    return render(
        request,
        "spa_app/dashboard/clients.html",
        {
            "clients": clients,
            "search_query": search_query,
            "active_page": "dashboard_clients",
        },
    )

@admin_required
def dashboard_client_detail(request, pk):
    client = get_object_or_404(ClientProfile.objects.select_related("user"), pk=pk)

    appointments = list(
        Appointment.objects.select_related(
            "employee__user",
            "service",
            "review",
        )
        .filter(client=client)
        .order_by("-start_datetime")
    )

    status_badges = {
        AppointmentStatus.CREATED: "secondary",
        AppointmentStatus.CONFIRMED: "primary",
        AppointmentStatus.COMPLETED: "success",
        AppointmentStatus.CANCELLED: "danger",
    }

    for appointment in appointments:
        appointment.employee_full_name = _build_user_full_name(
            appointment.employee.user
        )
        appointment.start_local = timezone.localtime(appointment.start_datetime)
        appointment.end_local = timezone.localtime(appointment.end_datetime)
        appointment.status_badge = status_badges.get(appointment.status, "secondary")
        appointment.has_review = hasattr(appointment, "review")
        appointment.payment_amount = appointment.service.price

        if appointment.is_paid:
            appointment.payment_status_label = "Оплачено"
            appointment.payment_status_badge = "success"
        else:
            appointment.payment_status_label = "Не оплачено"
            appointment.payment_status_badge = "warning"

    context = {
        "client": client,
        "client_full_name": _build_user_full_name(client.user),
        "client_phone": client.user.phone_number or "Не указано",
        "client_email": client.user.email or "Не указано",
        "client_created_at": timezone.localtime(client.created_at),
        "appointments": appointments,
        "appointments_count": len(appointments),
        "completed_count": sum(
            appointment.status == AppointmentStatus.COMPLETED
            for appointment in appointments
        ),
        "cancelled_count": sum(
            appointment.status == AppointmentStatus.CANCELLED
            for appointment in appointments
        ),
        "paid_total": sum(
            appointment.service.price
            for appointment in appointments
            if appointment.is_paid
        ),
        "last_appointment": appointments[0] if appointments else None,
        "active_page": "dashboard_clients",
    }

    return render(request, "spa_app/dashboard/client_detail.html", context)

@admin_required
def change_appointment_status(request, pk, new_status):
    appointment = get_object_or_404(Appointment, pk=pk)

    allowed_statuses = [
        AppointmentStatus.CANCELLED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.FAILED,
    ]

    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

    if new_status not in allowed_statuses:
        return HttpResponseForbidden("Недопустимый статус.")

    if new_status in [AppointmentStatus.COMPLETED, AppointmentStatus.FAILED]:
        if not appointment.is_paid:
            messages.error(request, "Сначала нужно принять оплату.")
            return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

    appointment.status = new_status
    appointment.save(update_fields=["status"])

    if new_status == AppointmentStatus.COMPLETED:
        _send_appointment_email_safely(
            request,
            appointment,
            "Спасибо за визит в Lotus Bloom",
            "spa_app/email/appointment_completed.html",
            "Письмо не доставлено.",
        )
        messages.success(request, "Запись завершена.")
    elif new_status == AppointmentStatus.FAILED:
        messages.warning(request, "Запись отмечена как завершенная неуспешно.")
    else:
        _send_appointment_email_safely(
            request,
            appointment,
            "Запись отменена",
            "spa_app/email/appointment_cancelled.html",
            "Письмо не доставлено.",
        )
        messages.success(request, "Запись отменена.")

    return redirect(request.META.get("HTTP_REFERER", "dashboard_appointments"))

@admin_required
def appointment_detail(request, pk):
    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "client__user",
            "employee__user",
            "service",
        ),
        pk=pk,
    )

    return render(
        request,
        "spa_app/dashboard/appointment_detail.html",
        {
            "appointment": appointment,
            "active_page": "dashboard_appointments",
        },
    )

@admin_required
def dashboard_services(request):
    services = (
        Service.objects.select_related("category")
        .prefetch_related("employees__user")
        .order_by("name")
    )

    return render(
        request,
        "spa_app/dashboard/services.html",
        {
            "services": services,
            "active_page": "dashboard_services",
        },
    )

@admin_required
def dashboard_service_categories(request):
    categories = (
        ServiceCategory.objects.prefetch_related(
            Prefetch(
                "services",
                queryset=Service.objects.prefetch_related("employees__user").order_by(
                    "name"
                ),
            )
        )
        .annotate(services_count=Count("services"))
        .order_by("name")
    )

    return render(
        request,
        "spa_app/dashboard/service_categories.html",
        {
            "categories": categories,
            "active_page": "dashboard_service_categories",
        },
    )

@admin_required
def service_category_create(request):
    if request.method == "POST":
        form = ServiceCategoryForm(request.POST, request.FILES)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Категория {category.name} добавлена.")
            return redirect("dashboard_service_categories")
        messages.error(request, "Не удалось добавить категорию. Проверьте поля формы.")
    else:
        form = ServiceCategoryForm()

    return render(
        request,
        "spa_app/dashboard/service_category_form.html",
        {
            "form": form,
            "title": "Добавить категорию услуг",
            "submit_label": "Создать категорию",
            "active_page": "dashboard_service_categories",
        },
    )

@admin_required
def service_category_update(request, pk):
    category = get_object_or_404(ServiceCategory, pk=pk)

    if request.method == "POST":
        form = ServiceCategoryForm(request.POST, request.FILES, instance=category)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Категория {category.name} обновлена.")
            return redirect("dashboard_service_categories")
        messages.error(request, "Не удалось обновить категорию. Проверьте поля формы.")
    else:
        form = ServiceCategoryForm(instance=category)

    return render(
        request,
        "spa_app/dashboard/service_category_form.html",
        {
            "form": form,
            "title": f"Редактировать категорию: {category.name}",
            "submit_label": "Сохранить изменения",
            "category": category,
            "active_page": "dashboard_service_categories",
        },
    )

@admin_required
def service_category_toggle_active(request, pk):
    category = get_object_or_404(ServiceCategory, pk=pk)

    if request.method == "POST":
        category.is_active = not category.is_active
        category.save(update_fields=["is_active"])
        messages.success(request, "Статус категории обновлен.")

    return redirect("dashboard_service_categories")

@admin_required
def service_create(request):
    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Услуга {service.name} добавлена.")
            if request.POST.get("next") == "categories":
                return redirect("dashboard_service_categories")
            return redirect("dashboard_services")
        messages.error(request, "Не удалось добавить услугу. Проверьте поля формы.")
    else:
        initial = {}
        category_id = request.GET.get("category")
        if category_id:
            initial["category"] = category_id
        form = ServiceForm(initial=initial)

    next_page = request.POST.get("next") or request.GET.get("next")

    return render(
        request,
        "spa_app/dashboard/service_form.html",
        {
            "form": form,
            "title": "Добавить услугу",
            "submit_label": "Создать услугу",
            "active_page": (
                "dashboard_service_categories"
                if next_page == "categories"
                else "dashboard_services"
            ),
            "next_page": next_page,
        },
    )

@admin_required
def service_update(request, pk):
    service = get_object_or_404(Service, pk=pk)

    if request.method == "POST":
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Услуга {service.name} обновлена.")
            if request.POST.get("next") == "categories":
                return redirect("dashboard_service_categories")
            return redirect("dashboard_services")
        messages.error(request, "Не удалось обновить услугу. Проверьте поля формы.")
    else:
        form = ServiceForm(instance=service)

    next_page = request.POST.get("next") or request.GET.get("next")

    return render(
        request,
        "spa_app/dashboard/service_form.html",
        {
            "form": form,
            "title": f"Редактировать услугу: {service.name}",
            "submit_label": "Сохранить изменения",
            "service": service,
            "active_page": (
                "dashboard_service_categories"
                if next_page == "categories"
                else "dashboard_services"
            ),
            "next_page": next_page,
        },
    )

@admin_required
def dashboard_employees(request):
    employees = list(
        EmployeeProfile.objects.select_related("user").order_by(
            "user__last_name",
            "user__first_name",
            "user__middle_name",
        )
    )

    for employee in employees:
        employee.full_name = _build_user_full_name(employee.user)
        employee.username = employee.user.username
        employee.phone = employee.user.phone_number or "Не указано"
        employee.email = employee.user.email or "Не указано"
        employee.schedule_summary = employee.get_work_schedule_display()

    return render(
        request,
        "spa_app/dashboard/employees.html",
        {
            "employees": employees,
            "active_page": "dashboard_employees",
        },
    )

@admin_required
def employee_create(request):
    if request.method == "POST":
        form = EmployeeCreateForm(request.POST, request.FILES)

        if form.is_valid():
            employee = _save_employee_from_form(form)

            if employee:
                try:
                    send_employee_invitation_email(request, employee.user)
                    messages.success(
                        request,
                        "Сотрудник создан. Ссылка для создания пароля отправлена на email.",
                    )
                except Exception:
                    messages.warning(
                        request, "Сотрудник создан, но письмо не отправилось."
                    )

                return redirect("dashboard_employees")

        messages.error(request, "Не удалось создать сотрудника. Проверьте поля формы.")
    else:
        form = EmployeeCreateForm()

    return render(
        request,
        "spa_app/dashboard/employee_create.html",
        {
            "form": form,
            "title": "Добавить сотрудника",
            "submit_label": "Создать сотрудника",
            "active_page": "dashboard_employees",
        },
    )

@admin_required
def employee_send_invitation(request, pk):
    if request.method != "POST":
        return redirect("dashboard_employees")

    employee = get_object_or_404(EmployeeProfile.objects.select_related("user"), pk=pk)

    try:
        send_employee_invitation_email(request, employee.user)
        messages.success(request, "Ссылка для создания пароля отправлена на email.")
    except Exception:
        messages.warning(request, "Письмо не отправилось.")

    return redirect("dashboard_employees")

@admin_required
def employee_update(request, pk):
    employee = get_object_or_404(
        EmployeeProfile.objects.select_related("user"),
        pk=pk,
    )

    if request.method == "POST":
        form = EmployeeCreateForm(
            request.POST,
            request.FILES,
            user_instance=employee.user,
        )

        if form.is_valid():
            updated_employee = _save_employee_from_form(form, employee=employee)

            if updated_employee:
                messages.success(request, "Сотрудник обновлен.")
                return redirect("dashboard_employees")

        messages.error(request, "Не удалось обновить сотрудника. Проверьте поля формы.")
    else:
        form = EmployeeCreateForm(
            initial=_build_employee_form_initial(employee),
            user_instance=employee.user,
        )

    return render(
        request,
        "spa_app/dashboard/employee_create.html",
        {
            "form": form,
            "title": f"Редактировать сотрудника: {_build_user_full_name(employee.user)}",
            "submit_label": "Сохранить изменения",
            "active_page": "dashboard_employees",
            "employee": employee,
        },
    )

@admin_required
def dashboard_exceptions(request):
    exceptions = list(
        ScheduleException.objects.select_related("employee__user").order_by(
            "-start_datetime"
        )
    )

    for exception in exceptions:
        exception.employee_full_name = _build_user_full_name(exception.employee.user)
        exception.status_label, exception.status_badge = _build_exception_status(
            exception
        )
        exception.start_local = timezone.localtime(exception.start_datetime)
        exception.end_local = timezone.localtime(exception.end_datetime)
        exception.reason_display = exception.reason or "Без комментария"

    return render(
        request,
        "spa_app/dashboard/exceptions.html",
        {
            "exceptions": exceptions,
            "active_page": "dashboard_exceptions",
        },
    )

@admin_required
def exception_create(request):
    if request.method == "POST":
        form = ScheduleExceptionForm(request.POST)

        if form.is_valid():
            exception = _save_exception_from_form(form)

            if exception:
                messages.success(request, "Отсутствие добавлено.")
                return redirect("dashboard_exceptions")

        messages.error(request, "Не удалось добавить отсутствие. Проверьте поля формы.")
    else:
        form = ScheduleExceptionForm()

    return render(
        request,
        "spa_app/dashboard/exception_form.html",
        {
            "form": form,
            "title": "Добавить отсутствие",
            "submit_label": "Сохранить отсутствие",
            "active_page": "dashboard_exceptions",
        },
    )

@admin_required
def exception_update(request, pk):
    exception = get_object_or_404(
        ScheduleException.objects.select_related("employee__user"),
        pk=pk,
    )

    if request.method == "POST":
        form = ScheduleExceptionForm(request.POST, instance=exception)

        if form.is_valid():
            updated_exception = _save_exception_from_form(form, exception=exception)

            if updated_exception:
                messages.success(request, "Отсутствие обновлено.")
                return redirect("dashboard_exceptions")

        messages.error(request, "Не удалось обновить отсутствие. Проверьте поля формы.")
    else:
        form = ScheduleExceptionForm(instance=exception)

    return render(
        request,
        "spa_app/dashboard/exception_form.html",
        {
            "form": form,
            "title": "Редактировать отсутствие",
            "submit_label": "Сохранить изменения",
            "active_page": "dashboard_exceptions",
            "exception": exception,
        },
    )

@admin_required
def dashboard_reviews(request):
    reviews = (
        Review.objects.select_related(
            "client__user",
            "service",
            "appointment",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "spa_app/dashboard/reviews.html",
        {
            "reviews": reviews,
            "active_page": "dashboard_reviews",
        },
    )


@admin_required
def review_publish(request, pk):
    review = get_object_or_404(Review, pk=pk)

    if request.method == "POST":
        review.is_published = True
        review.is_rejected = False
        review.save(update_fields=["is_published", "is_rejected"])
        messages.success(request, "Отзыв опубликован.")

    return redirect("dashboard_reviews")


@admin_required
def review_reject(request, pk):
    review = get_object_or_404(Review, pk=pk)

    if request.method == "POST":
        review.is_published = False
        review.is_rejected = True
        review.save(update_fields=["is_published", "is_rejected"])
        messages.success(request, "Отзыв отклонен.")

    return redirect("dashboard_reviews")


@admin_required
def review_return_to_moderation(request, pk):
    review = get_object_or_404(Review, pk=pk)

    if request.method == "POST":
        review.is_published = False
        review.is_rejected = False
        review.save(update_fields=["is_published", "is_rejected"])
        messages.success(request, "Отзыв возвращен на модерацию.")

    return redirect("dashboard_reviews")




@admin_required
def dashboard_analytics(request):
    today = timezone.localdate()
    now = timezone.now()

    month_start_date, month_start, next_month_start = _month_start_bounds(today)

    month_appointments = Appointment.objects.select_related(
        "service",
        "employee__user",
    ).filter(
        start_datetime__gte=month_start,
        start_datetime__lt=next_month_start,
    )

    paid_month_appointments = month_appointments.filter(
        is_paid=True,
        is_refunded=False,
    )

    refunded_month_appointments = Appointment.objects.select_related("service").filter(
    is_refunded=True,
    refunded_at__gte=month_start,
    refunded_at__lt=next_month_start,
)

    month_appointments_count = month_appointments.count()

    completed_this_month = month_appointments.filter(
        status=AppointmentStatus.COMPLETED
    ).count()

    failed_this_month = month_appointments.filter(
        status=AppointmentStatus.FAILED
    ).count()

    cancelled_this_month = month_appointments.filter(
        status=AppointmentStatus.CANCELLED
    ).count()

    completion_rate = (
        round(completed_this_month / month_appointments_count * 100, 1)
        if month_appointments_count
        else 0
    )

    failed_rate = (
        round(failed_this_month / month_appointments_count * 100, 1)
        if month_appointments_count
        else 0
    )

    cancellation_rate = (
        round(cancelled_this_month / month_appointments_count * 100, 1)
        if month_appointments_count
        else 0
    )

    revenue_this_month = (
        paid_month_appointments.aggregate(
            total=Coalesce(
                Sum("service__price"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )

    refunded_this_month = (
        refunded_month_appointments.aggregate(
            total=Coalesce(
                Sum("service__price"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )

    average_ticket = (
        paid_month_appointments.aggregate(avg=Avg("service__price"))["avg"]
        or Decimal("0.00")
    )

    cash_total = (
        paid_month_appointments.filter(payment_method=PaymentMethod.CASH).aggregate(
            total=Coalesce(
                Sum("service__price"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )

    card_total = (
        paid_month_appointments.filter(payment_method=PaymentMethod.CARD).aggregate(
            total=Coalesce(
                Sum("service__price"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )

    cash_count = paid_month_appointments.filter(
        payment_method=PaymentMethod.CASH
    ).count()

    card_count = paid_month_appointments.filter(
        payment_method=PaymentMethod.CARD
    ).count()

    upcoming_week_appointments = Appointment.objects.filter(
        start_datetime__gte=now,
        start_datetime__lt=now + timedelta(days=7),
        status__in=[AppointmentStatus.CREATED, AppointmentStatus.CONFIRMED],
    ).count()

    active_clients_this_month = (
        ClientProfile.objects.filter(
            appointments__start_datetime__gte=month_start,
            appointments__start_datetime__lt=next_month_start,
        )
        .distinct()
        .count()
    )

    repeat_clients_count = (
        ClientProfile.objects.annotate(appointments_count=Count("appointments"))
        .filter(appointments_count__gt=1)
        .count()
    )

    average_rating = Review.objects.filter(is_published=True).aggregate(
        avg=Avg("rating")
    )["avg"]

    moderation_reviews_count = Review.objects.filter(
        is_published=False,
        is_rejected=False,
    ).count()

    top_services = list(
        Service.objects.annotate(
            completed_count=Count(
                "appointments",
                filter=Q(appointments__status=AppointmentStatus.COMPLETED),
            )
        )
        .filter(completed_count__gt=0)
        .order_by("-completed_count")[:6]
    )

    employee_stats = list(
        EmployeeProfile.objects.select_related("user")
        .annotate(
            total_appointments=Count(
                "appointments",
                filter=Q(
                    appointments__start_datetime__gte=month_start,
                    appointments__start_datetime__lt=next_month_start,
                ),
            ),
            completed_appointments=Count(
                "appointments",
                filter=Q(
                    appointments__start_datetime__gte=month_start,
                    appointments__start_datetime__lt=next_month_start,
                    appointments__status=AppointmentStatus.COMPLETED,
                ),
            ),
            failed_appointments=Count(
                "appointments",
                filter=Q(
                    appointments__start_datetime__gte=month_start,
                    appointments__start_datetime__lt=next_month_start,
                    appointments__status=AppointmentStatus.FAILED,
                ),
            ),
            refunded_appointments=Count(
                "appointments",
                filter=Q(
                    appointments__start_datetime__gte=month_start,
                    appointments__start_datetime__lt=next_month_start,
                    appointments__is_refunded=True,
                ),
            ),
            revenue=Coalesce(
                Sum(
                    "appointments__service__price",
                    filter=Q(
                        appointments__start_datetime__gte=month_start,
                        appointments__start_datetime__lt=next_month_start,
                        appointments__is_paid=True,
                        appointments__is_refunded=False,
                    ),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        .filter(total_appointments__gt=0)
        .order_by("-revenue", "-total_appointments")[:10]
    )

    for employee in employee_stats:
        employee.full_name = _build_user_full_name(employee.user)
        employee.load_rate = (
            round(employee.completed_appointments / employee.total_appointments * 100, 1)
            if employee.total_appointments
            else 0
        )

    monthly_trend = []

    for offset in range(-5, 1):
        trend_month = _shift_month(month_start_date, offset)
        trend_start = timezone.make_aware(
            datetime.combine(trend_month, datetime.min.time())
        )
        next_trend_month = _shift_month(trend_month, 1)
        trend_end = timezone.make_aware(
            datetime.combine(next_trend_month, datetime.min.time())
        )

        appointments = Appointment.objects.filter(
            is_paid=True,
            is_refunded=False,
            paid_at__gte=trend_start,
            paid_at__lt=trend_end,
        )

        monthly_trend.append(
            {
                "label": _month_label(trend_month),
                "count": appointments.count(),
                "total": appointments.aggregate(
                    total=Coalesce(
                        Sum("service__price"),
                        Value(Decimal("0.00")),
                        output_field=DecimalField(max_digits=10, decimal_places=2),
                    )
                )["total"],
            }
        )

    chart_data = {
        "months": {
            "labels": [row["label"] for row in monthly_trend],
            "revenue": [float(row["total"] or 0) for row in monthly_trend],
        },
        "payments": {
            "labels": ["Наличные", "Карта"],
            "values": [cash_count, card_count],
        },
        "services": {
            "labels": [service.name for service in top_services],
            "values": [service.completed_count for service in top_services],
        },
        "employees": {
            "labels": [employee.full_name for employee in employee_stats],
            "revenue": [float(employee.revenue or 0) for employee in employee_stats],
            "appointments": [employee.total_appointments for employee in employee_stats],
        },
    }

    return render(
        request,
        "spa_app/dashboard/analytics.html",
        {
            "month_label": _month_label(month_start_date),
            "revenue_this_month": revenue_this_month,
            "refunded_this_month": refunded_this_month,
            "average_ticket": average_ticket,
            "cash_total": cash_total,
            "card_total": card_total,
            "cash_count": cash_count,
            "card_count": card_count,
            "month_appointments_count": month_appointments_count,
            "completed_this_month": completed_this_month,
            "failed_this_month": failed_this_month,
            "cancelled_this_month": cancelled_this_month,
            "completion_rate": completion_rate,
            "failed_rate": failed_rate,
            "cancellation_rate": cancellation_rate,
            "upcoming_week_appointments": upcoming_week_appointments,
            "active_clients_this_month": active_clients_this_month,
            "repeat_clients_count": repeat_clients_count,
            "average_rating": average_rating,
            "moderation_reviews_count": moderation_reviews_count,
            "monthly_trend": monthly_trend,
            "chart_data": chart_data,
            "employee_stats": employee_stats,
            "active_page": "dashboard_analytics",
        },
    )
