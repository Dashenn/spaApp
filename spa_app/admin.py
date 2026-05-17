from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Appointment,
    ClientProfile,
    EmployeeProfile,
    Review,
    ScheduleException,
    Service,
    ServiceCategory,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "last_name",
        "first_name",
        "middle_name",
        "email",
        "phone_number",
        "role",
        "is_staff",
        "is_active",
    )
    list_filter = ("role", "is_staff", "is_active")
    search_fields = (
        "username",
        "last_name",
        "first_name",
        "middle_name",
        "phone_number",
        "email",
    )
    ordering = ("last_name", "first_name", "username")

    fieldsets = (
        ("Учетные данные", {"fields": ("username", "password")}),
        (
            "Личная информация",
            {
                "fields": (
                    "last_name",
                    "first_name",
                    "middle_name",
                    "email",
                    "phone_number",
                )
            },
        ),
        (
            "Права доступа",
            {
                "fields": (
                    "role",
                    "is_staff",
                    "is_active",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            "Создание пользователя",
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "last_name",
                    "first_name",
                    "middle_name",
                    "email",
                    "phone_number",
                    "role",
                    "is_staff",
                    "is_superuser",
                    "is_active",
                ),
            },
        ),
    )


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__phone_number",
    )


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "position",
        "experience_years",
        "is_active",
        "work_start_time",
        "work_end_time",
    )
    list_filter = ("is_active", "position")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "position",
    )


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "duration_minutes", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name",)
    filter_horizontal = ("employees",)




@admin.register(ScheduleException)
class ScheduleExceptionAdmin(admin.ModelAdmin):
    list_display = ("employee", "exception_type", "start_datetime", "end_datetime")
    list_filter = ("exception_type", "employee")
    search_fields = (
        "employee__user__last_name",
        "employee__user__first_name",
        "reason",
    )


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "employee",
        "service",
        "start_datetime",
        "end_datetime",
        "status",
    )
    list_filter = ("status", "employee", "service")
    search_fields = (
        "client__user__first_name",
        "client__user__last_name",
        "client__user__phone_number",
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("client", "appointment", "service", "rating", "created_at", "is_published")
    list_filter = ("rating", "is_published")
