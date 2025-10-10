import schedule as schedule
from django.contrib import admin
from django.contrib.admin import register
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group, User as AUser
from django.utils.html import format_html

from backend.models import User, Otp, Token, PasswordResetToken, Category, Slide, Product, ProductOption, ProductImage, \
    PageItem, Order, OrderedProduct, Notification, ContactInfo, InformMe, AppVersion, ServiceOption, \
    ServiceCategory, Service, ServiceImage, ServiceBooking, ServicePageItem, Vendor, VendorToken
from .models import User
from .decorators import password_protected_view

admin.site.unregister(Group)
admin.site.unregister(AUser)

admin.site.site_header = "rental Cloths Admin"
admin.site.site_title = "rental-Cloths  Admin"
admin.site.index_title = "Welcome to rental Cloths  Admin Panel"


@register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'email', 'phone', 'fullname', 'address', 'pincode', 'created_at']
    fieldsets = (
        ('User info', {
            'fields': ('email', 'phone', 'fullname', 'password',)
        }),
        ('Address info', {
            'fields': ('name', 'address', 'contact_no', 'pincode', 'district', 'state',)
        }),
    )
    readonly_fields = ['password', 'email','phone','fullname','name','address','pincode','district', 'state','contact_no']
    search_fields = ['id','email','phone','fullname','address','pincode']
    search_help_text =  "Search by id,email,phone,fullname,address,pincode"



@register(Otp)
class OtpAdmin(admin.ModelAdmin):
    list_display = ['phone', 'otp', 'validity', 'verified']

    def has_add_permission(self, request):
        return False

@register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ['token', 'fcmtoken', 'user', 'created_at']

    def has_add_permission(self, request):
        return False


@register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ['token', 'user', 'validity']

    def has_add_permission(self, request):
        return False


@register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'position', 'image']




@register(Slide)
class SlideAdmin(admin.ModelAdmin):
    list_display = ['position', 'image']





class ProductOptionInline(admin.TabularInline):
    list = ['id', 'product', 'option', 'quantity']
    model = ProductOption
    extra = 0
    show_change_link = True


@register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductOptionInline]
    list_display = ['id', 'vendor', 'category', 'title', 'price', 'offer_price', 'delivery_charge', 'cod', 'created_at',
                    'updated_at']  # Added 'vendor'
    readonly_fields = ['star_1', 'star_2', 'star_3', 'star_4', 'star_5']
    list_filter = ['cod', 'category', 'vendor']  # Added 'vendor'
    search_fields = ['id', 'title', 'vendor__name', 'vendor__vendor_id']  # Added vendor search
    search_help_text = "Search by Id, title, vendor name, vendor ID"

    def get_queryset(self, request):
        """Optionally show only products from specific vendors"""
        qs = super().get_queryset(request)
        return qs.select_related('vendor', 'category')

class ProductImageInline(admin.TabularInline):
    list = ['image', 'position']
    model = ProductImage
    extra = 0
    min_num = 1


@register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline]
    list_display = ['id', 'product', 'option', 'quantity']
    search_fields = ['product__title','option','quantity']
    search_help_text = 'Search by, Product, Option, Quantity'


@register(PageItem)
class PageItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'position', 'image', 'category', 'viewtype']
    filter_horizontal = ['product_options']
    list_filter = ['viewtype','category']
    search_fields = ['title']
    search_help_text = "Search by title"




@register(OrderedProduct)
class OrderedProductAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity',
                    'status', 'rating', 'created_at', 'updated_at']
    readonly_fields = ['order','product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'rating']
    search_fields = ['id']
    search_help_text = "Search by Id"
    list_filter = ['status']
    ordering = ['-created_at']

    def save_model(self, request, ordered_product, form, change):
        super(OrderedProductAdmin, self).save_model( request, ordered_product, form, change)
        user = ordered_product.order.user
        title = "ORDER "+ordered_product.status
        body = "Your "+ordered_product.product_option.__str__()+" has been "+ordered_product.status+"."
        image = ordered_product.product_option.images_set.first().image
        print("ORDER STATUS: "+title)

    def has_add_permission(self, request):
        return False



class OrderedProductInline(admin.TabularInline):
    model = OrderedProduct
    fields = ['product_option', 'quantity', 'product_price', 'tx_price', 'delivery_price', 'status', 'rating']  # Changed from 'list' to 'fields'
    readonly_fields = ['product_option', 'product_price', 'tx_price', 'delivery_price', 'quantity', 'status', 'rating']
    show_change_link = True
    extra = 0

    def has_add_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderedProductInline]
    list_display = ['id','seen', 'user', 'tx_amount', 'payment_mode', 'address', 'tx_id', 'tx_status', 'tx_time', 'tx_msg',
                    'from_cart', 'created_at', 'updated_at']
    list_filter = ['payment_mode', 'tx_status', 'from_cart']
    ordering = ['-created_at']
    readonly_fields = ['user', 'tx_amount', 'payment_mode', 'tx_id', 'tx_time', 'tx_msg', 'from_cart']
    search_fields = ['id','user__email','address','tx_id', ]
    search_help_text = "Search by Id, user, address, tx_id"

    def has_add_permission(self, request):
        return False


@register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'body', 'image', 'seen', 'created_at']



@admin.register(ContactInfo)
class ContactInfoAdmin(admin.ModelAdmin):
    list_display = ['id', 'phone_number']
    fields = ['phone_number']
    search_fields = ['phone_number']
    search_help_text = "Search by phone number"

    def has_add_permission(self, request):
        # Allow only one ContactInfo entry
        if ContactInfo.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(InformMe)
class InformMeAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'product', 'product_option',
        'price', 'offer_price', 'created_at'
    )
    list_filter = ('created_at', 'product')
    search_fields = ('user__email', 'product__title')
    readonly_fields = ('price', 'offer_price', 'created_at')


@admin.register(AppVersion)
class AppVersionAdmin(admin.ModelAdmin):
    list_display = [
        'platform',
        'version_name',
        'version_code',
        'is_active',
        'is_force_update',
        'created_at'
    ]
    list_filter = ['platform', 'is_force_update', 'is_active', 'created_at']
    search_fields = ['version_name', 'update_message', 'release_notes']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Version Information', {
            'fields': ('platform', 'version_name', 'version_code', 'store_url'),
            'description': 'Basic version details and store URL'
        }),
        ('Update Policy', {
            'fields': ('min_supported_version', 'min_supported_code', 'is_force_update', 'is_active'),
            'description': 'Control update behavior and requirements'
        }),
        ('User Messages', {
            'fields': ('update_message', 'release_notes'),
            'description': 'Messages shown to users in the update dialog'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        # Deactivate other versions for the same platform when a new active version is saved
        if obj.is_active:
            AppVersion.objects.filter(
                platform=obj.platform,
                is_active=True
            ).exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)

    # Add custom actions
    actions = ['make_active', 'make_inactive', 'make_force_update', 'remove_force_update']

    def make_active(self, request, queryset):
        for obj in queryset:
            # Deactivate other versions for the same platform
            AppVersion.objects.filter(platform=obj.platform, is_active=True).update(is_active=False)
            obj.is_active = True
            obj.save()
        self.message_user(request, f"{queryset.count()} version(s) activated.")

    make_active.short_description = "Activate selected versions"

    def make_inactive(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"{queryset.count()} version(s) deactivated.")

    make_inactive.short_description = "Deactivate selected versions"

    def make_force_update(self, request, queryset):
        queryset.update(is_force_update=True)
        self.message_user(request, f"{queryset.count()} version(s) set to force update.")

    make_force_update.short_description = "Set as force update"

    def remove_force_update(self, request, queryset):
        queryset.update(is_force_update=False)
        self.message_user(request, f"{queryset.count()} version(s) set to optional update.")

    remove_force_update.short_description = "Remove force update"


@register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'position', 'icon', 'color', 'image']
    list_editable = ['position']
    ordering = ['position']


class ServiceOptionInline(admin.TabularInline):
    model = ServiceOption
    extra = 0
    show_change_link = True


@register(Service)
class ServiceAdmin(admin.ModelAdmin):
    inlines = [ServiceOptionInline]
    list_display = [
        'id', 'title', 'category', 'provider_name', 'base_price',
        'rating', 'availability', 'created_at'
    ]
    list_filter = ['category', 'availability', 'created_at']
    search_fields = ['title', 'provider_name', 'provider_phone', 'location']
    search_help_text = "Search by title, provider name, phone, or location"
    readonly_fields = ['rating', 'total_reviews', 'created_at', 'updated_at']

    fieldsets = (
        ('Service Information', {
            'fields': ('category', 'title', 'description', 'base_price', 'availability', 'location')
        }),
        ('Provider Details', {
            'fields': ('provider_name', 'provider_phone', 'provider_email', 'experience_years')
        }),
        ('Ratings & Reviews', {
            'fields': ('rating', 'total_reviews'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class ServiceImageInline(admin.TabularInline):
    model = ServiceImage
    extra = 0
    min_num = 1


@register(ServiceOption)
class ServiceOptionAdmin(admin.ModelAdmin):
    inlines = [ServiceImageInline]
    list_display = ['id', 'service', 'option_name', 'price', 'duration', 'available']
    list_filter = ['available', 'service__category']
    search_fields = ['service__title', 'option_name']
    search_help_text = 'Search by service title or option name'


@register(ServicePageItem)
class ServicePageItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'position', 'category', 'viewtype']
    list_filter = ['viewtype', 'category']
    filter_horizontal = ['service_options']
    search_fields = ['title']
    search_help_text = "Search by title"
    ordering = ['category__position', 'position']


@register(ServiceBooking)
class ServiceBookingAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'service_option', 'booking_date', 'booking_time',
        'status', 'payment_status', 'total_amount', 'created_at'
    ]
    list_filter = ['status', 'payment_status', 'booking_date', 'created_at']
    search_fields = [
        'id', 'user__email', 'customer_name', 'customer_phone',
        'service_option__service__title'
    ]
    search_help_text = "Search by booking ID, user email, customer name, phone, or service"
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Booking Information', {
            'fields': ('user', 'service_option', 'booking_date', 'booking_time', 'duration')
        }),
        ('Customer Details', {
            'fields': ('customer_name', 'customer_phone', 'customer_address')
        }),
        ('Payment & Status', {
            'fields': ('total_amount', 'payment_status', 'status')
        }),
        ('Additional Info', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']


# ============== VENDOR ADMIN ==============

from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import make_password
from backend.models import Vendor


class VendorAdminForm(forms.ModelForm):
    """Custom form to handle password securely in the admin."""
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Enter a password to set or update. Leave blank to keep the existing one."
    )

    class Meta:
        model = Vendor
        fields = '__all__'

    def save(self, commit=True):
        vendor = super().save(commit=False)
        raw_password = self.cleaned_data.get('password')
        # Hash the password only if a new one is entered (avoid double hashing)
        if raw_password and not raw_password.startswith('pbkdf2_'):
            vendor.password = make_password(raw_password)
        if commit:
            vendor.save()
        return vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    """Admin panel configuration for Vendor model."""
    form = VendorAdminForm
    list_display = ['vendor_id', 'name', 'email', 'phone', 'is_active', 'created_at']
    search_fields = ['vendor_id', 'name', 'email', 'phone', 'gst_number']
    list_filter = ['is_active', 'created_at', 'updated_at']
    readonly_fields = ['vendor_id', 'created_at', 'updated_at']
    fieldsets = (
        ('Vendor Info', {
            'fields': ('vendor_id', 'name', 'email', 'phone', 'password')
        }),
        ('Business Details', {
            'fields': ('business_address', 'gst_number')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def save_model(self, request, obj, form, change):
        """Ensure password is hashed when created/updated."""
        raw_password = form.cleaned_data.get('password')
        if raw_password and not raw_password.startswith('pbkdf2_'):
            obj.set_password(raw_password)
        super().save_model(request, obj, form, change)



@register(VendorToken)
class VendorTokenAdmin(admin.ModelAdmin):
    list_display = ['vendor', 'token_preview', 'created_at']
    readonly_fields = ['token', 'vendor', 'fcmtoken', 'created_at']
    search_fields = ['vendor__vendor_id', 'vendor__name', 'vendor__email']

    def token_preview(self, obj):
        return f"{obj.token[:20]}..."

    token_preview.short_description = 'Token'

    def has_add_permission(self, request):
        return False
