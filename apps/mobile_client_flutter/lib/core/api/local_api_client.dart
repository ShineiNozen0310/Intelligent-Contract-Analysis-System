import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as crypto;
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

import '../models/review_models.dart';

class LocalApiClient {
  LocalApiClient(String baseUrl) : _baseUrl = _normalizeBase(baseUrl);

  String _baseUrl;

  String get baseUrl => _baseUrl;

  set baseUrl(String value) {
    _baseUrl = _normalizeBase(value);
  }

  Future<Map<String, dynamic>> health() async {
    try {
      final resp = await http
          .get(_uri('/api/health/'))
          .timeout(const Duration(seconds: 10));
      return _readJson(resp);
    } on TimeoutException {
      throw const ApiException(
        code: 'E-NET-TIMEOUT',
        message: '连接本地服务超时，请稍后重试。',
        suggestions: ['请确认后端服务已启动，再重试健康检查。'],
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
        suggestions: const ['请执行 start_all.bat start 启动服务。'],
      );
    }
  }

  Future<Map<String, dynamic>> preflight() async {
    try {
      final resp = await http
          .get(_uri('/api/preflight/'))
          .timeout(const Duration(seconds: 12));
      final data = _readJson(resp);
      data['http_status'] = resp.statusCode;
      return data;
    } on TimeoutException {
      throw const ApiException(
        code: 'E-PREFLIGHT-TIMEOUT',
        message: '启动检查超时，请稍后重试。',
        suggestions: ['请确认本地服务正在启动中，等待 5-10 秒后重试。'],
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-PREFLIGHT-CONNECTION',
        message: '启动检查失败，无法连接本地服务。',
        detail: e.message,
        suggestions: const ['请执行 start_all.bat start 启动服务。'],
      );
    }
  }

  Future<int> startAnalyze(File pdfFile) async {
    try {
      final req = http.MultipartRequest('POST', _uri('/api/start/'));
      req.files.add(await http.MultipartFile.fromPath('file', pdfFile.path));
      final streamed = await req.send().timeout(const Duration(seconds: 120));
      final resp = await http.Response.fromStream(streamed);
      final data = _readJson(resp);
      if (resp.statusCode >= 400 || data['ok'] != true) {
        throw _buildApiException(
          data,
          fallbackCode: 'E-START-FAILED',
          fallbackMessage: '任务启动失败，请稍后重试。',
        );
      }
      final id = data['job_id'];
      if (id is int) {
        return id;
      }
      if (id is String) {
        final parsed = int.tryParse(id);
        if (parsed != null) {
          return parsed;
        }
      }
      throw const ApiException(
        code: 'E-INVALID-JOB-ID',
        message: '系统返回了无效任务编号，请重试。',
      );
    } on TimeoutException {
      throw const ApiException(
        code: 'E-START-TIMEOUT',
        message: '任务提交超时，请稍后重试。',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
        suggestions: const ['请执行 start_all.bat status 检查服务状态。'],
      );
    }
  }

  Future<JobStatus> fetchStatus(int jobId) async {
    try {
      final resp = await http
          .get(_uri('/api/status/$jobId/'))
          .timeout(const Duration(seconds: 20));
      final data = _readJson(resp);
      if (resp.statusCode >= 400) {
        throw _buildApiException(
          data,
          fallbackCode: 'E-STATUS-FAILED',
          fallbackMessage: '任务状态查询失败。',
        );
      }
      return JobStatus.fromJson(data);
    } on TimeoutException {
      throw const ApiException(
        code: 'E-STATUS-TIMEOUT',
        message: '查询任务状态超时，请稍后重试。',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
      );
    }
  }

  Future<JobResult> fetchResult(int jobId) async {
    try {
      final resp = await http
          .get(_uri('/api/result/$jobId/'))
          .timeout(const Duration(seconds: 20));
      final data = _readJson(resp);
      if (resp.statusCode >= 400) {
        throw _buildApiException(
          data,
          fallbackCode: 'E-RESULT-FAILED',
          fallbackMessage: '审查结果查询失败。',
        );
      }
      return JobResult.fromJson(data);
    } on TimeoutException {
      throw const ApiException(
        code: 'E-RESULT-TIMEOUT',
        message: '查询审查结果超时，请稍后重试。',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
      );
    }
  }

  Future<String> exportPdf(int jobId, {String? preferredName}) async {
    try {
      final resp = await http
          .get(_uri('/api/export_pdf/$jobId/'))
          .timeout(const Duration(seconds: 120));
      if (resp.statusCode != 200) {
        final data = _readJson(resp);
        throw _buildApiException(
          data,
          fallbackCode: 'E-EXPORT-PDF-FAILED',
          fallbackMessage: 'PDF 导出失败。',
        );
      }
      return _saveBinary(
        resp.bodyBytes,
        preferredName: preferredName ?? 'contract_review_job_$jobId.pdf',
      );
    } on TimeoutException {
      throw const ApiException(
        code: 'E-EXPORT-PDF-TIMEOUT',
        message: 'PDF 导出超时，请稍后重试。',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
      );
    }
  }

  Future<String> exportLogsBundle() async {
    try {
      final resp = await http
          .get(_uri('/api/export_logs/'))
          .timeout(const Duration(seconds: 60));
      if (resp.statusCode != 200) {
        final data = _readJson(resp);
        throw _buildApiException(
          data,
          fallbackCode: 'E-EXPORT-LOGS-FAILED',
          fallbackMessage: '诊断包导出失败。',
        );
      }
      final fileName =
          'contract_review_diagnostics_${DateTime.now().millisecondsSinceEpoch}.zip';
      return _saveBinary(resp.bodyBytes, preferredName: fileName);
    } on TimeoutException {
      throw const ApiException(
        code: 'E-EXPORT-LOGS-TIMEOUT',
        message: '导出诊断包超时，请稍后重试。',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '无法连接本地服务。',
        detail: e.message,
      );
    }
  }

  Future<UpdateInfo> checkUpdate() async {
    try {
      final resp = await http
          .get(_uri('/api/update/check/'))
          .timeout(const Duration(seconds: 8));
      final data = _readJson(resp);
      if (resp.statusCode >= 400) {
        throw _buildApiException(
          data,
          fallbackCode: 'E-UPDATE-CHECK-FAILED',
          fallbackMessage: '\u68c0\u67e5\u66f4\u65b0\u5931\u8d25\u3002',
        );
      }
      return UpdateInfo.fromJson(data);
    } on TimeoutException {
      throw const ApiException(
        code: 'E-UPDATE-CHECK-TIMEOUT',
        message: '\u68c0\u67e5\u66f4\u65b0\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '\u65e0\u6cd5\u8fde\u63a5\u672c\u5730\u670d\u52a1\u3002',
        detail: e.message,
      );
    }
  }

  Future<String> downloadUpdatePackage(UpdateInfo info) async {
    final url = info.downloadUrl.trim();
    if (url.isEmpty) {
      throw const ApiException(
        code: 'E-UPDATE-NO-URL',
        message: '\u5f53\u524d\u7248\u672c\u65e0\u53ef\u4e0b\u8f7d\u5347\u7ea7\u5305\u3002',
      );
    }

    try {
      final uri = Uri.parse(url);
      final resp = await http.get(uri).timeout(const Duration(minutes: 5));
      if (resp.statusCode != 200) {
        throw ApiException(
          code: 'E-UPDATE-DOWNLOAD-FAILED',
          message: '\u5347\u7ea7\u5305\u4e0b\u8f7d\u5931\u8d25\uff08HTTP ${resp.statusCode}\uff09\u3002',
        );
      }

      final name = _deriveUpdateFileName(uri);
      final outputPath = await _saveBinary(resp.bodyBytes, preferredName: name);

      final expected = info.sha256.trim().toLowerCase();
      if (expected.isNotEmpty) {
        final actual = _computeSha256Hex(resp.bodyBytes).toLowerCase();
        if (actual != expected) {
          throw ApiException(
            code: 'E-UPDATE-SHA256-MISMATCH',
            message: '\u5347\u7ea7\u5305\u6821\u9a8c\u5931\u8d25\uff08SHA256 \u4e0d\u5339\u914d\uff09\u3002',
            detail: 'expected=$expected actual=$actual',
          );
        }
      }
      return outputPath;
    } on TimeoutException {
      throw const ApiException(
        code: 'E-UPDATE-DOWNLOAD-TIMEOUT',
        message: '\u5347\u7ea7\u5305\u4e0b\u8f7d\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002',
      );
    } on SocketException catch (e) {
      throw ApiException(
        code: 'E-NET-CONNECTION',
        message: '\u65e0\u6cd5\u8fde\u63a5\u66f4\u65b0\u6e90\u3002',
        detail: e.message,
      );
    }
  }

  Uri _uri(String path) => Uri.parse('$_baseUrl$path');

  static String _normalizeBase(String input) {
    var s = input.trim();
    if (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    if (s.endsWith('/contract')) {
      return s;
    }
    return '$s/contract';
  }

  Map<String, dynamic> _readJson(http.Response resp) {
    if (resp.body.isEmpty) {
      return {'ok': false, 'error': '空响应'};
    }
    try {
      final decoded = jsonDecode(resp.body);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      }
      return {'ok': false, 'error': '响应格式无效'};
    } catch (_) {
      return {'ok': false, 'error': resp.body};
    }
  }

  ApiException _buildApiException(
    Map<String, dynamic> data, {
    required String fallbackCode,
    required String fallbackMessage,
  }) {
    final code = (data['error_code'] ?? fallbackCode).toString();
    final message =
        (data['error_message'] ?? data['detail'] ?? data['error'] ?? fallbackMessage)
            .toString();
    final detail = (data['error_detail'] ?? '').toString();
    final rawSuggestions = data['suggestions'];
    final suggestions = <String>[];
    if (rawSuggestions is List) {
      for (final item in rawSuggestions) {
        final text = item.toString().trim();
        if (text.isNotEmpty) {
          suggestions.add(text);
        }
      }
    }
    return ApiException(
      code: code,
      message: message,
      detail: detail,
      suggestions: suggestions,
    );
  }

  Future<String> _saveBinary(Uint8List bytes,
      {required String preferredName}) async {
    final targetDir = await _resolveOutputDirectory();
    final target =
        File('${targetDir.path}${Platform.pathSeparator}$preferredName');
    await target.writeAsBytes(bytes, flush: true);
    return target.path;
  }

  Future<Directory> _resolveOutputDirectory() async {
    final downloadDir = await getDownloadsDirectory();
    if (downloadDir != null) {
      return downloadDir;
    }
    return getApplicationDocumentsDirectory();
  }

  String _deriveUpdateFileName(Uri uri) {
    final fromPath = uri.pathSegments.isEmpty ? '' : uri.pathSegments.last;
    final cleaned = fromPath.trim();
    if (cleaned.isNotEmpty) {
      return cleaned;
    }
    return 'contract_review_update_${DateTime.now().millisecondsSinceEpoch}.exe';
  }

  String _computeSha256Hex(Uint8List bytes) {
    return crypto.sha256.convert(bytes).toString();
  }
}

class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.detail = '',
    this.suggestions = const [],
  });

  final String code;
  final String message;
  final String detail;
  final List<String> suggestions;

  String get displayText {
    final lines = <String>['[$code] $message'];
    if (suggestions.isNotEmpty) {
      lines.add('建议：${suggestions.take(3).join('；')}');
    }
    return lines.join('\n');
  }

  @override
  String toString() => displayText;
}



