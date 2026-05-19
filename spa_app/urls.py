from django.urls import path

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    path("home", views.index, name="home"),
    path("about/", views.about, name="about"),
    path("services/", views.services, name="services"),
    path("contact/", views.contact, name="contact"),
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "employees/set-password/<uidb64>/<token>/",
        views.employee_set_password,
        name="employee_set_password",
    ),
    path("account/", views.account_view, name="account"),
    path("employee-crm/", views.employee_portal, name="employee_portal"),
    path(
        "account/appointments/<int:appointment_id>/review/",
        views.appointment_review,
        name="appointment_review",
    ),
    path("get-service-employees/", views.get_service_employees, name="get_service_employees"),
    path("get-available-times/", views.get_available_times, name="get_available_times"),
    path("dashboard/", views.dashboard_home, name="dashboard_home"),
    path("dashboard/clients/", views.dashboard_clients, name="dashboard_clients"),
    path(
        "dashboard/clients/<int:pk>/",
        views.dashboard_client_detail,
        name="dashboard_client_detail",
    ),
    path("dashboard/appointments/", views.dashboard_appointments, name="dashboard_appointments"),
    path(
        "dashboard/appointments/<int:pk>/status/<str:new_status>/",
        views.change_appointment_status,
        name="change_appointment_status",
    ),
    path(
        "dashboard/appointments/<int:pk>/",
        views.appointment_detail,
        name="appointment_detail",
    ),
    path("dashboard/services/", views.dashboard_services, name="dashboard_services"),
    path(
        "dashboard/service-categories/",
        views.dashboard_service_categories,
        name="dashboard_service_categories",
    ),
    path(
        "dashboard/service-categories/create/",
        views.service_category_create,
        name="service_category_create",
    ),
    path(
        "dashboard/service-categories/<int:pk>/edit/",
        views.service_category_update,
        name="service_category_update",
    ),
    path(
        "dashboard/service-categories/<int:pk>/toggle-active/",
        views.service_category_toggle_active,
        name="service_category_toggle_active",
    ),
    path("dashboard/services/create/", views.service_create, name="service_create"),
    path("dashboard/services/<int:pk>/edit/", views.service_update, name="service_update"),
    path("dashboard/employees/", views.dashboard_employees, name="dashboard_employees"),
    path("dashboard/employees/create/", views.employee_create, name="employee_create"),
    path(
        "dashboard/employees/<int:pk>/send-invitation/",
        views.employee_send_invitation,
        name="employee_send_invitation",
    ),
    path("dashboard/employees/<int:pk>/edit/", views.employee_update, name="employee_update"),
    path("dashboard/exceptions/", views.dashboard_exceptions, name="dashboard_exceptions"),
    path("dashboard/exceptions/create/", views.exception_create, name="exception_create"),
    path("dashboard/exceptions/<int:pk>/edit/", views.exception_update, name="exception_update"),
    path("dashboard/analytics/", views.dashboard_analytics, name="dashboard_analytics"),
    path(
    "dashboard/appointments/<int:pk>/confirm/",
    views.confirm_appointment,
    name="confirm_appointment",
),
path(
    "dashboard/appointments/<int:pk>/accept-payment/",
    views.accept_appointment_payment,
    name="accept_appointment_payment",
),
path(
    "dashboard/appointments/<int:pk>/refund/",
    views.refund_appointment_payment,
    name="refund_appointment_payment",
),

path(
    "account/appointments/<int:pk>/confirm/",
    views.client_confirm_appointment,
    name="client_confirm_appointment",
),

path(
    "account/appointments/<int:pk>/cancel/",
    views.client_cancel_appointment,
    name="client_cancel_appointment",
),
path("dashboard/reviews/", views.dashboard_reviews, name="dashboard_reviews"),
path(
    "dashboard/reviews/<int:pk>/publish/",
    views.review_publish,
    name="review_publish",
),
path(
    "dashboard/reviews/<int:pk>/reject/",
    views.review_reject,
    name="review_reject",
),
path(
    "dashboard/reviews/<int:pk>/moderation/",
    views.review_return_to_moderation,
    name="review_return_to_moderation",
),
path(
    "clients/set-password/<uidb64>/<token>/",
    views.client_set_password,
    name="client_set_password",
),
   
]
