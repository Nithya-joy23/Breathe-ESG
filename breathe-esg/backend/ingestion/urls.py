from django.urls import path
from . import views

urlpatterns = [
    # Authentication endpoints
    path('auth/login/', views.login_view, name='login'),
    path('auth/register/', views.register_view, name='register'),
    path('auth/logout/', views.logout_view, name='logout'),
    path('auth/me/', views.current_user, name='current_user'),

    # Upload endpoints
    path('ingest/sap/', views.upload_sap, name='upload_sap'),
    path('ingest/utility/', views.upload_utility, name='upload_utility'),
    path('ingest/travel/', views.upload_travel, name='upload_travel'),
    
    # Record endpoints
    path('records/', views.list_records, name='list_records'),
    path('records/<int:pk>/approve/', views.approve_record, name='approve_record'),
    path('records/<int:pk>/reject/', views.reject_record, name='reject_record'),
    path('records/<int:pk>/edit/', views.edit_record, name='edit_record'),
    path('records/<int:pk>/undo/', views.undo_record, name='undo_record'),
    path('records/<int:pk>/delete/', views.delete_record, name='delete_record'),
    path('records/<int:pk>/lock/', views.lock_record, name='lock_record'),
    path('records/<int:pk>/audit/', views.get_record_audit, name='record_audit'),
    
    # Batch operations
    path('records/lock/', views.lock_records, name='lock_records'),
    path('records/summary/', views.get_summary, name='summary'),
    path('records/failed/', views.list_failed_records, name='failed_records'),
    path('records/failed/<int:pk>/retry/', views.retry_failed_record, name='retry_failed_record'),
    path('runs/latest/failures/', views.get_latest_failures, name='latest_failures'),
    path('tenants/', views.list_tenants, name='tenants'),
    
    # Run details
    path('runs/<int:run_id>/', views.get_run_details, name='run_details'),
]
