from django.urls import path

from backend.views import (
    # Authentication
    request_otp, verify_otp, create_account, login, userdata, resend_otp, logout, save_device_token, device_token_status,

    # Home and navigation
    home_screen_data, get_page_items_by_category, get_categories_with_page_items,
    home_banners, admin_home_banner_list_create, admin_home_banner_detail,

    # Products
    search_products, category_products, all_categories, page_item_products,
    product_details_with_dates, check_date_availability, get_product_booked_dates,
    update_product_positions,

    # Cart and Wishlist
    add_to_cart, remove_from_cart, get_cart_items, add_to_cart_with_date,
    add_to_wishlist, remove_from_wishlist, get_wishlist_items,
    validate_cart, move_to_wishlist, clear_cart, cart_summary, bulk_update_cart,
    apply_coupon, get_cart_items_enhanced,
    get_wishlist_items_enhanced, add_to_wishlist_enhanced,
    remove_from_wishlist_enhanced, move_wishlist_to_cart, share_wishlist,
    undo_wishlist_removal, clear_wishlist,

    # Orders - CRITICAL IMPORT
    create_order_with_bookings,  # ✅ This is the key import
    get_order_tracking, get_user_orders, get_order_confirmation_status,
    update_order_rating, cancel_order,

    # Profile
    slides, get_profile, update_profile, upload_profile_image, edit_profile,
    get_addresses, add_address, update_address, delete_address,

    # Services
    get_service_details, create_service_booking, get_service_page_items,
    get_service_categories, get_service_subcategories, get_all_services, get_user_service_bookings,
    cancel_booking, reschedule_booking, get_service_availability,
    get_available_time_slots, rate_service_booking,

    # Password Reset
    forgot_password, verify_forgot_password_otp, resend_forgot_password_otp, reset_password,

    # Vendor
    vendor_login, vendor_dashboard, vendor_get_products, vendor_get_product_detail,
    vendor_create_product, vendor_update_product, vendor_delete_product,
    vendor_create_product_option, vendor_update_product_option,
    vendor_delete_product_option, vendor_upload_product_image, vendor_delete_product_image,
    vendor_get_categories, vendor_bulk_update_stock,
    vendor_orders, vendor_order_detail, vendor_accept_order, vendor_reject_order, vendor_logout, set_default_address,
    get_serviceable_locations, update_cart_item_quantity, get_service_wishlist, add_service_to_wishlist,
    remove_service_from_wishlist, check_service_in_wishlist, change_password, guest_login, get_home_page_item_products

)

urlpatterns = [
    # =============================================================================
    # AUTHENTICATION ENDPOINTS
    # =============================================================================
    path('request_otp/', request_otp, name='request_otp'),
    path('resend_otp/', resend_otp, name='resend_otp'),
    path('verify_otp/', verify_otp, name='verify_otp'),
    path('create_account/', create_account, name='create_account'),
    path('login/', login, name='login'),
    path('logout/', logout, name='logout'),
    path('userdata/', userdata, name='userdata'),
    path('save-device-token/', save_device_token, name='save_device_token'),
    path('device-token-status/', device_token_status, name='device_token_status'),
    path('guest-login/', guest_login, name='guest_login'),

    # =============================================================================
    # HOME SCREEN AND NAVIGATION ENDPOINTS
    # =============================================================================
    path('home/', home_screen_data, name='home_screen_data'),
    path('category/<int:category_id>/page-items/', get_page_items_by_category, name='category_page_items'),
    path('categories/with-page-items/', get_categories_with_page_items, name='categories_with_page_items'),
    path('slides/', slides, name='slides'),
    path('home-banners/', home_banners, name='home_banners'),

    # =============================================================================
    # ADMIN - Home Banners (POST/PUT/DELETE; GET list in admin)
    # =============================================================================
    path('admin/home-banners/', admin_home_banner_list_create, name='admin_home_banner_list_create'),
    path('admin/home-banners/<int:banner_id>/', admin_home_banner_detail, name='admin_home_banner_detail'),

    # =============================================================================
    # PRODUCT SEARCH AND FILTERING
    # =============================================================================
    path('search/', search_products, name='search_products'),
    path('category/<int:category_id>/products/', category_products, name='category_products'),
    path('categories/', all_categories, name='all_categories'),
    path('page-item-products/', page_item_products, name='page_item_products'),

    # =============================================================================
    # PRODUCT DETAILS WITH RENTAL
    # =============================================================================
    path('product/<uuid:product_id>/details-with-dates/', product_details_with_dates,
         name='product_details_with_dates'),
    path('product/check-date-availability/', check_date_availability, name='check_date_availability'),
    path('product/<uuid:product_id>/booked-dates/', get_product_booked_dates, name='get_product_booked_dates'),
    path('products/update-positions/', update_product_positions, name='update_product_positions'),

    # =============================================================================
    # CART MANAGEMENT
    # =============================================================================
    path('cart/add/', add_to_cart, name='add_to_cart'),
    path('cart/add-with-date/', add_to_cart_with_date, name='add_to_cart_with_date'),
    path('cart/remove/<str:item_id>/', remove_from_cart, name='remove_from_cart'),
    path('cart/', get_cart_items, name='get_cart_items'),
    path('cart/enhanced/', get_cart_items_enhanced, name='get_cart_items_enhanced'),
    path('cart/apply-coupon/', apply_coupon, name='apply_coupon'),
    path('cart/bulk-update/', bulk_update_cart, name='bulk_update_cart'),
    path('cart/summary/', cart_summary, name='cart_summary'),
    path('cart/clear/', clear_cart, name='clear_cart'),
    path('cart/move-to-wishlist/', move_to_wishlist, name='move_to_wishlist'),
    path('cart/validate/', validate_cart, name='validate_cart'),

    # =============================================================================
    # WISHLIST MANAGEMENT
    # =============================================================================
    path('wishlist/add/', add_to_wishlist, name='add_to_wishlist'),
    path('wishlist/remove/<uuid:product_option_id>/', remove_from_wishlist, name='remove_from_wishlist'),
    path('wishlist/', get_wishlist_items, name='get_wishlist_items'),
    path('wishlist/enhanced/', get_wishlist_items_enhanced, name='get_wishlist_items_enhanced'),
    path('wishlist/add-enhanced/', add_to_wishlist_enhanced, name='add_to_wishlist_enhanced'),
    path('wishlist/remove-enhanced/<uuid:product_option_id>/', remove_from_wishlist_enhanced,
         name='remove_from_wishlist_enhanced'),
    path('wishlist/move-to-cart/', move_wishlist_to_cart, name='move_wishlist_to_cart'),
    path('wishlist/share/', share_wishlist, name='share_wishlist'),
    path('wishlist/undo/', undo_wishlist_removal, name='undo_wishlist_removal'),
    path('wishlist/clear/', clear_wishlist, name='clear_wishlist'),

    # =============================================================================
    # ORDER MANAGEMENT - FIXED
    # =============================================================================
    # ✅ CRITICAL: This must point to create_order_with_bookings
    path('orders/create/', create_order_with_bookings, name='create_order_with_bookings'),

    path('orders/<uuid:order_id>/tracking/', get_order_tracking, name='get_order_tracking'),
    path('orders/', get_user_orders, name='get_user_orders'),
    path('orders/products/<uuid:ordered_product_id>/rating/', update_order_rating, name='update_order_rating'),
    path('orders/<uuid:order_id>/cancel/', cancel_order, name='cancel_order'),
    path('orders/<uuid:order_id>/confirmation-status/', get_order_confirmation_status,
         name='order_confirmation_status'),

    # =============================================================================
    # PROFILE MANAGEMENT
    # =============================================================================
    path('profile/', get_profile, name='get_profile'),
    path('profile/update/', update_profile, name='update_profile'),
    path('profile/upload-image/', upload_profile_image, name='upload_profile_image'),
    path('profile/edit/', edit_profile, name='edit_profile'),


    # =============================================================================
    # ADDRESS MANAGEMENT
    # =============================================================================
    path('addresses/', get_addresses, name='get_addresses'),
    path('addresses/add/', add_address, name='add_address'),
    path('addresses/<int:address_id>/update/', update_address, name='update_address'),
    path('addresses/<int:address_id>/delete/', delete_address, name='delete_address'),
    path('addresses/<int:address_id>/set-default/', set_default_address, name='set_default_address'),


    # =============================================================================
    # SERVICES
    # =============================================================================
    path('services/categories/', get_service_categories, name='get_service_categories'),
    path('services/categories/<int:category_id>/subcategories/', get_service_subcategories, name='get_service_subcategories'),
    path('services/', get_all_services, name='get_all_services'),
    path('services/<uuid:service_id>/', get_service_details, name='get_service_details'),
    path('services/<uuid:service_id>/availability/', get_service_availability, name='get_service_availability'),
    path('services/<uuid:service_id>/time-slots/', get_available_time_slots, name='get_available_time_slots'),
    path('services/page-items/', get_service_page_items, name='get_service_page_items'),
    path('services/bookings/create/', create_service_booking, name='create_service_booking'),
    path('services/bookings/', get_user_service_bookings, name='get_user_service_bookings'),
    path('services/bookings/rate/', rate_service_booking, name='rate_service_booking'),
    path('services/bookings/<uuid:booking_id>/cancel/', cancel_booking, name='cancel_booking'),
    path('services/bookings/<uuid:booking_id>/reschedule/', reschedule_booking, name='reschedule_booking'),

# ============== SERVICE WISHLIST ==============
    path('services/wishlist/', get_service_wishlist, name='get_service_wishlist'),
    path('services/wishlist/add/', add_service_to_wishlist, name='add_service_to_wishlist'),
    path('services/wishlist/remove/<uuid:service_id>/', remove_service_from_wishlist, name='remove_service_from_wishlist'),
    path('services/wishlist/check/', check_service_in_wishlist, name='check_service_in_wishlist'),

    # =============================================================================
    # PASSWORD RESET
    # =============================================================================
    path('forgot-password/', forgot_password, name='forgot_password'),
    path('forgot-password/verify-otp/', verify_forgot_password_otp, name='verify_forgot_password_otp'),
    path('forgot-password/resend-otp/', resend_forgot_password_otp, name='resend_forgot_password_otp'),
    path('reset-password/', reset_password, name='reset_password'),
    path('change-password/', change_password, name='change_password'),

    # =============================================================================
    # VENDOR ENDPOINTS
    # =============================================================================
    path('vendor/login/', vendor_login, name='vendor_login'),
    path('vendor/logout/', vendor_logout, name='vendor_logout'),
    path('vendor/dashboard/', vendor_dashboard, name='vendor_dashboard'),
    path('vendor/products/', vendor_get_products, name='vendor_get_products'),
    path('vendor/products/<uuid:product_id>/', vendor_get_product_detail, name='vendor_get_product_detail'),
    path('vendor/products/create/', vendor_create_product, name='vendor_create_product'),
    path('vendor/products/<uuid:product_id>/update/', vendor_update_product, name='vendor_update_product'),
    path('vendor/products/<uuid:product_id>/delete/', vendor_delete_product, name='vendor_delete_product'),
    path('vendor/product-options/create/', vendor_create_product_option, name='vendor_create_product_option'),
    path('vendor/product-options/<uuid:option_id>/update/', vendor_update_product_option,
         name='vendor_update_product_option'),
    path('vendor/product-options/<uuid:option_id>/delete/', vendor_delete_product_option,
         name='vendor_delete_product_option'),
    path('vendor/images/upload/', vendor_upload_product_image, name='vendor_upload_product_image'),
    path('vendor/images/<int:image_id>/delete/', vendor_delete_product_image, name='vendor_delete_product_image'),
    path('vendor/categories/', vendor_get_categories, name='vendor_get_categories'),
    path('vendor/stock/bulk-update/', vendor_bulk_update_stock, name='vendor_bulk_update_stock'),
    path('vendor/orders/', vendor_orders, name='vendor_orders'),
    path('vendor/orders/<uuid:order_id>/', vendor_order_detail, name='vendor_order_detail'),
    path('vendor/orders/<uuid:order_id>/accept/', vendor_accept_order, name='vendor_accept_order'),
    path('vendor/orders/<uuid:order_id>/reject/', vendor_reject_order, name='vendor_reject_order'),
    path('serviceable-locations/', get_serviceable_locations, name='get_serviceable_locations'),


    # In the CART MANAGEMENT section, add:
    path('cart/update/<str:item_id>/', update_cart_item_quantity, name='update_cart_item_quantity'),

    path('home-page-items/<int:home_page_item_id>/products/', get_home_page_item_products, name='get_home_page_item_products'),







]
