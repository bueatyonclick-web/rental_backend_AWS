from django.urls import path

from backend.views import (
    # Existing authentication endpoints
    request_otp, verify_otp, create_account, login, userdata, resend_otp, logout,

    # Existing home and page items endpoints
    home_screen_data, get_page_items_by_category, get_categories_with_page_items,

    # Existing product endpoints
    search_products, category_products, all_categories, page_item_products,

    # Existing cart and wishlist endpoints
    add_to_cart, remove_from_cart, get_cart_items,
    add_to_wishlist, remove_from_wishlist, get_wishlist_items, product_details,
    validate_cart, move_to_wishlist, clear_cart, cart_summary, bulk_update_cart,
    apply_coupon, get_cart_items_enhanced, get_order_tracking, get_user_orders,
    update_order_rating, cancel_order, slides, get_profile, update_profile,
    upload_profile_image, edit_profile, get_addresses, add_address, update_address,
    delete_address, get_wishlist_items_enhanced, add_to_wishlist_enhanced,
    remove_from_wishlist_enhanced, move_wishlist_to_cart, share_wishlist,
    undo_wishlist_removal, clear_wishlist, get_service_details, create_order,
    create_service_booking, get_service_page_items, get_service_categories, get_all_services, get_user_service_bookings,
    cancel_booking, reschedule_booking, get_service_availability, get_available_time_slots, rate_service_booking,
    forgot_password, verify_forgot_password_otp, resend_forgot_password_otp, reset_password,
    vendor_login, vendor_dashboard,vendor_get_products, vendor_get_product_detail,
    vendor_create_product, vendor_update_product, vendor_delete_product,vendor_create_product_option, vendor_update_product_option,
    vendor_delete_product_option,vendor_upload_product_image, vendor_delete_product_image,vendor_get_categories, vendor_bulk_update_stock

)

urlpatterns = [
    # =============================================================================
    # AUTHENTICATION ENDPOINTS
    # =============================================================================

    # OTP and Account Management
    path('request_otp/', request_otp, name='request_otp'),
    path('resend_otp/', resend_otp, name='resend_otp'),
    path('verify_otp/', verify_otp, name='verify_otp'),
    path('create_account/', create_account, name='create_account'),

    # Login and Session Management
    path('login/', login, name='login'),
    path('logout/', logout, name='logout'),
    path('userdata/', userdata, name='userdata'),

    # =============================================================================
    # HOME SCREEN AND NAVIGATION ENDPOINTS
    # =============================================================================

    # Main home screen data (user info, categories, slides, page items, products)
    path('home/', home_screen_data, name='home_screen_data'),

    # Page items management
    path('category/<int:category_id>/page-items/', get_page_items_by_category, name='category_page_items'),
    path('categories/with-page-items/', get_categories_with_page_items, name='categories_with_page_items'),

    # Promotional slides
    path('slides/', slides, name='slides'),

    # =============================================================================
    # VIEW ALL ENDPOINTS - PRODUCT SEARCH AND FILTERING
    # =============================================================================

    # Advanced product search with filtering and sorting
    path('search/', search_products, name='search_products'),

    # Category-specific products with filtering and sorting
    path('category/<int:category_id>/products/', category_products, name='category_products'),

    # All categories view
    path('categories/', all_categories, name='all_categories'),

    # Page item products with filtering and sorting
    path('page-item-products/', page_item_products, name='page_item_products'),

    # =============================================================================
    # PRODUCT DETAILS ENDPOINTS
    # =============================================================================

    # Enhanced product details with ratings, reviews, and related products
    path('product/<uuid:product_id>/', product_details, name='product_details'),

    # =============================================================================
    # CART MANAGEMENT ENDPOINTS
    # =============================================================================

    # Basic cart operations
    path('cart/add/', add_to_cart, name='add_to_cart'),
    path('cart/remove/<uuid:product_option_id>/', remove_from_cart, name='remove_from_cart'),
    path('cart/', get_cart_items, name='get_cart_items'),

    # Enhanced cart operations
    path('cart/enhanced/', get_cart_items_enhanced, name='get_cart_items_enhanced'),
    path('cart/apply-coupon/', apply_coupon, name='apply_coupon'),
    path('cart/bulk-update/', bulk_update_cart, name='bulk_update_cart'),
    path('cart/summary/', cart_summary, name='cart_summary'),
    path('cart/clear/', clear_cart, name='clear_cart'),
    path('cart/move-to-wishlist/', move_to_wishlist, name='move_to_wishlist'),
    path('cart/validate/', validate_cart, name='validate_cart'),

    # =============================================================================
    # WISHLIST MANAGEMENT ENDPOINTS
    # =============================================================================

    # Basic wishlist operations
    path('wishlist/add/', add_to_wishlist, name='add_to_wishlist'),
    path('wishlist/remove/<uuid:product_option_id>/', remove_from_wishlist, name='remove_from_wishlist'),
    path('wishlist/', get_wishlist_items, name='get_wishlist_items'),

    # Enhanced wishlist operations
    path('wishlist/enhanced/', get_wishlist_items_enhanced, name='get_wishlist_items_enhanced'),
    path('wishlist/add-enhanced/', add_to_wishlist_enhanced, name='add_to_wishlist_enhanced'),
    path('wishlist/remove-enhanced/<uuid:product_option_id>/', remove_from_wishlist_enhanced,
         name='remove_from_wishlist_enhanced'),
    path('wishlist/move-to-cart/', move_wishlist_to_cart, name='move_wishlist_to_cart'),
    path('wishlist/share/', share_wishlist, name='share_wishlist'),
    path('wishlist/undo/', undo_wishlist_removal, name='undo_wishlist_removal'),
    path('wishlist/clear/', clear_wishlist, name='clear_wishlist'),

    # =============================================================================
    # ORDER MANAGEMENT ENDPOINTS
    # =============================================================================

    # Order creation
    path('orders/create/', create_order, name='create_order'),

    # Order tracking and management
    path('orders/<uuid:order_id>/tracking/', get_order_tracking, name='get_order_tracking'),
    path('orders/', get_user_orders, name='get_user_orders'),
    path('orders/products/<uuid:ordered_product_id>/rating/', update_order_rating, name='update_order_rating'),
    path('orders/<uuid:order_id>/cancel/', cancel_order, name='cancel_order'),

    # =============================================================================
    # PROFILE MANAGEMENT ENDPOINTS
    # =============================================================================

    # Profile operations
    path('profile/', get_profile, name='get_profile'),
    path('profile/update/', update_profile, name='update_profile'),
    path('profile/upload-image/', upload_profile_image, name='upload_profile_image'),
    path('profile/edit/', edit_profile, name='edit_profile'),

    # =============================================================================
    # ADDRESS MANAGEMENT ENDPOINTS
    # =============================================================================

    path('addresses/', get_addresses, name='get_addresses'),
    path('addresses/add/', add_address, name='add_address'),
    path('addresses/<int:address_id>/update/', update_address, name='update_address'),
    path('addresses/<int:address_id>/delete/', delete_address, name='delete_address'),

    # Service categories
    path('services/categories/', get_service_categories, name='get_service_categories'),

    # Services listing and details
    path('services/', get_all_services, name='get_all_services'),
    path('services/<uuid:service_id>/', get_service_details, name='get_service_details'),

    # Service availability and time slots
    path('services/<uuid:service_id>/availability/', get_service_availability, name='get_service_availability'),
    path('services/<uuid:service_id>/time-slots/', get_available_time_slots, name='get_available_time_slots'),

    # Service page items
    path('services/page-items/', get_service_page_items, name='get_service_page_items'),

    # Service bookings
    path('services/bookings/create/', create_service_booking, name='create_service_booking'),
    path('services/bookings/', get_user_service_bookings, name='get_user_service_bookings'),
    path('services/bookings/rate/', rate_service_booking, name='rate_service_booking'),  # ADD THIS
    path('services/bookings/<uuid:booking_id>/cancel/', cancel_booking, name='cancel_booking'),
    path('services/bookings/<uuid:booking_id>/reschedule/', reschedule_booking, name='reschedule_booking'),

    # Forgot password flow
    path('forgot-password/', forgot_password, name='forgot_password'),
    path('forgot-password/verify-otp/', verify_forgot_password_otp, name='verify_forgot_password_otp'),
    path('forgot-password/resend-otp/', resend_forgot_password_otp, name='resend_forgot_password_otp'),
    path('reset-password/', reset_password, name='reset_password'),

    # =============================================================================
    # VENDOR AUTHENTICATION
    # =============================================================================
    path('vendor/login/', vendor_login, name='vendor_login'),
    path('vendor/dashboard/', vendor_dashboard, name='vendor_dashboard'),

    # =============================================================================
    # VENDOR PRODUCT MANAGEMENT
    # =============================================================================
    path('vendor/products/', vendor_get_products, name='vendor_get_products'),
    path('vendor/products/<uuid:product_id>/', vendor_get_product_detail, name='vendor_get_product_detail'),
    path('vendor/products/create/', vendor_create_product, name='vendor_create_product'),
    path('vendor/products/<uuid:product_id>/update/', vendor_update_product, name='vendor_update_product'),
    path('vendor/products/<uuid:product_id>/delete/', vendor_delete_product, name='vendor_delete_product'),

    # =============================================================================
    # VENDOR PRODUCT OPTION MANAGEMENT
    # =============================================================================
    path('vendor/product-options/create/', vendor_create_product_option, name='vendor_create_product_option'),
    path('vendor/product-options/<uuid:option_id>/update/', vendor_update_product_option,
         name='vendor_update_product_option'),
    path('vendor/product-options/<uuid:option_id>/delete/', vendor_delete_product_option,
         name='vendor_delete_product_option'),

    # =============================================================================
    # VENDOR IMAGE MANAGEMENT
    # =============================================================================
    path('vendor/images/upload/', vendor_upload_product_image, name='vendor_upload_product_image'),
    path('vendor/images/<int:image_id>/delete/', vendor_delete_product_image, name='vendor_delete_product_image'),

    # =============================================================================
    # VENDOR UTILITIES
    # =============================================================================
    path('vendor/categories/', vendor_get_categories, name='vendor_get_categories'),
    path('vendor/stock/bulk-update/', vendor_bulk_update_stock, name='vendor_bulk_update_stock'),

]
