from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.formats import date_format, time_format
from django.utils.http import urlsafe_base64_encode


def build_employee_password_setup_url(request, user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("employee_set_password", args=[uidb64, token])
    return request.build_absolute_uri(path)


def send_employee_invitation_email(request, user):
    invite_url = build_employee_password_setup_url(request, user)
    message = render_to_string(
        "spa_app/email/employee_invitation.html",
        {
            "employee_user": user,
            "invite_url": invite_url,
        },
    )

    sent_count = send_mail(
        "Доступ сотрудника Lotus Bloom",
        "",
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        html_message=message,
    )
    if sent_count == 0:
        raise RuntimeError("Письмо сотруднику не было отправлено.")

    return invite_url


def send_booking_confirmation_email(appointment):
    start_datetime = timezone.localtime(appointment.start_datetime)
    end_datetime = timezone.localtime(appointment.end_datetime)
    client_user = appointment.client.user

    message = render_to_string(
        "spa_app/email/email_message.html",
        {
            "booking": appointment,
            "client_user": client_user,
            "date_str": date_format(start_datetime, "j E Y"),
            "time_str": time_format(start_datetime, "H:i"),
            "end_time_str": time_format(end_datetime, "H:i"),
        },
    )

    if not client_user.email:
        return

    send_mail(
        "Подтверждение записи",
        "",
        settings.DEFAULT_FROM_EMAIL,
        [client_user.email],
        html_message=message,
    )
