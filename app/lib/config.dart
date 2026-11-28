// Build-time configuration.
//
// Every value comes from `--dart-define` so that no project values live in
// the source. See README.md for the full list of variables.

import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart' show kIsWeb;

class AppConfig {
  AppConfig._();

  static const String firebaseApiKey = String.fromEnvironment('FIREBASE_API_KEY');
  static const String firebaseAuthDomain = String.fromEnvironment('FIREBASE_AUTH_DOMAIN');
  static const String firebaseProjectId = String.fromEnvironment('FIREBASE_PROJECT_ID');
  static const String firebaseAppId = String.fromEnvironment('FIREBASE_APP_ID');
  static const String firebaseMessagingSenderId =
      String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID');

  /// Base URL of the sidegig server. Empty means "the origin this page was
  /// served from", which is how the web build runs behind the server.
  static const String apiBase = String.fromEnvironment('SIDEGIG_API');

  /// True when the build carries enough Firebase values to start sign-in.
  static bool get hasFirebase =>
      firebaseApiKey.isNotEmpty &&
      firebaseProjectId.isNotEmpty &&
      firebaseAppId.isNotEmpty &&
      firebaseMessagingSenderId.isNotEmpty;

  static FirebaseOptions get firebaseOptions => FirebaseOptions(
        apiKey: firebaseApiKey,
        appId: firebaseAppId,
        messagingSenderId: firebaseMessagingSenderId,
        projectId: firebaseProjectId,
        authDomain: firebaseAuthDomain.isEmpty ? null : firebaseAuthDomain,
      );

  /// The server origin without a trailing slash, or null when it cannot be
  /// known (a phone build without SIDEGIG_API has no page origin).
  static String? get serverBase {
    var base = apiBase.trim();
    if (base.isEmpty) {
      if (!kIsWeb) return null;
      base = Uri.base.origin;
    }
    while (base.endsWith('/')) {
      base = base.substring(0, base.length - 1);
    }
    return base;
  }
}
