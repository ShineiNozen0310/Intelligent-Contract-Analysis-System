import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../../core/api/local_api_client.dart';
import '../../core/models/review_models.dart';

class ReviewController extends ChangeNotifier {
  ReviewController({String? baseUrl})
      : _baseUrl = baseUrl ?? _defaultBaseUrl(),
        _client = LocalApiClient(baseUrl ?? _defaultBaseUrl());

  final LocalApiClient _client;

  String _baseUrl;
  bool _loading = false;
  bool _polling = false;
  int? _jobId;
  String _status = 'idle';
  int _progress = 0;
  String _stage = '';
  String _filePath = '';
  String _fileName = '';
  String _message = '';
  String _error = '';
  String _errorCode = '';
  String _reportMarkdown = '';
  Map<String, dynamic>? _reportPayload;
  Map<String, dynamic>? _resultJson;
  Map<String, dynamic> _runtimeMeta = <String, dynamic>{};
  String _exportedPath = '';
  String _diagnosticsPath = '';
  DateTime? _reviewStartedAt;
  DateTime? _reviewFinishedAt;

  bool _updateAvailable = false;
  String _updateCurrentVersion = '';
  String _updateLatestVersion = '';
  String _updateDownloadUrl = '';
  String _updateSha256 = '';
  String _updateNotes = '';
  String _updatePackagePath = '';

  Timer? _pollTimer;

  String get baseUrl => _baseUrl;
  bool get loading => _loading;
  bool get polling => _polling;
  int? get jobId => _jobId;
  String get status => _status;
  int get progress => _progress;
  String get stage => _stage;
  String get filePath => _filePath;
  String get fileName => _fileName;
  String get message => _message;
  String get error => _error;
  String get errorCode => _errorCode;
  String get reportMarkdown => _reportMarkdown;
  Map<String, dynamic>? get reportPayload => _reportPayload;
  Map<String, dynamic>? get resultJson => _resultJson;
  Map<String, dynamic> get runtimeMeta => _runtimeMeta;
  String get exportedPath => _exportedPath;
  String get diagnosticsPath => _diagnosticsPath;
  bool get updateAvailable => _updateAvailable;
  String get updateCurrentVersion => _updateCurrentVersion;
  String get updateLatestVersion => _updateLatestVersion;
  String get updateDownloadUrl => _updateDownloadUrl;
  String get updateSha256 => _updateSha256;
  String get updateNotes => _updateNotes;
  String get updatePackagePath => _updatePackagePath;

  Map<String, dynamic> get llmCallMeta {
    final node = _runtimeMeta['llm_call'];
    if (node is Map<String, dynamic>) {
      return node;
    }
    return const <String, dynamic>{};
  }

  int get llmAttempts {
    final value = llmCallMeta['attempts'];
    if (value is int) {
      return value;
    }
    if (value is String) {
      return int.tryParse(value) ?? 0;
    }
    return 0;
  }

  int get llmMaxAttempts {
    final value = llmCallMeta['max_attempts'];
    if (value is int) {
      return value;
    }
    if (value is String) {
      return int.tryParse(value) ?? 0;
    }
    return 0;
  }

  int get llmRetryCount {
    final attempts = llmAttempts;
    if (attempts <= 1) {
      return 0;
    }
    return attempts - 1;
  }

  double get totalSeconds {
    final value = _runtimeMeta['total_seconds'];
    if (value is num) {
      return value.toDouble();
    }
    if (value is String) {
      return double.tryParse(value) ?? 0.0;
    }
    return 0.0;
  }

  Map<String, double> get stageTimings {
    final node = _runtimeMeta['stage_timings'];
    if (node is! Map) {
      return const <String, double>{};
    }
    final out = <String, double>{};
    for (final entry in node.entries) {
      final key = entry.key.toString();
      final value = _toDouble(entry.value);
      if (value != null) {
        out[key] = value;
      }
    }
    return out;
  }

  Duration? get reviewElapsed {
    final start = _reviewStartedAt;
    if (start == null) {
      return null;
    }
    final end = _reviewFinishedAt ?? DateTime.now();
    if (end.isBefore(start)) {
      return Duration.zero;
    }
    return end.difference(start);
  }

  String get reviewElapsedText {
    final elapsed = reviewElapsed;
    if (elapsed == null) {
      return '--';
    }
    return _formatDuration(elapsed);
  }

  String get prettyJson {
    final data = _resultJson;
    if (data == null) {
      return '';
    }
    return const JsonEncoder.withIndent('  ').convert(data);
  }

  Future<void> updateBaseUrl(String value) async {
    _baseUrl = value.trim();
    _client.baseUrl = _baseUrl;
    notifyListeners();
  }

  void applyStartupNotice(String notice) {
    final text = notice.trim();
    if (text.isEmpty) {
      return;
    }
    _message = text;
    notifyListeners();
  }

  Future<void> checkHealth() async {
    await _runGuarded(() async {
      Map<String, dynamic> report = const {'ok': false};
      Object? lastError;
      for (var i = 0; i < 4; i++) {
        try {
          report = await _client.preflight();
          final ok = report['ok'] == true;
          if (ok) {
            _message = '启动检查通过，服务已就绪。';
            _error = '';
            _errorCode = '';
            return;
          }
          _applyPreflightFailure(report);
        } catch (e) {
          lastError = e;
        }
        if (i != 3) {
          await Future<void>.delayed(const Duration(milliseconds: 850));
        }
      }

      if (lastError != null) {
        _applyError(lastError);
      } else {
        _applyPreflightFailure(report);
      }
    });
  }

  Future<void> checkUpdateSilently() async {
    try {
      final info = await _client.checkUpdate();
      _applyUpdateInfo(info);
      if (_updateAvailable) {
        if (_message.trim().isEmpty && _error.trim().isEmpty) {
          _message = '检测到新版本 $_updateLatestVersion，可下载升级包。';
        }
      }
      notifyListeners();
    } catch (_) {
      // non-blocking, ignore silent update check errors.
    }
  }

  Future<void> checkUpdateManually() async {
    await _runGuarded(() async {
      final info = await _client.checkUpdate();
      _applyUpdateInfo(info);
      if (_updateAvailable) {
        _message = '发现新版本：$_updateLatestVersion';
      } else {
        _message = '当前已是最新版本（$_updateCurrentVersion）。';
      }
    });
  }

  Future<void> downloadUpdatePackage() async {
    if (!_updateAvailable) {
      _message = '当前没有可下载的新版本。';
      notifyListeners();
      return;
    }

    await _runGuarded(() async {
      final info = UpdateInfo(
        ok: true,
        currentVersion: _updateCurrentVersion,
        latestVersion: _updateLatestVersion,
        hasUpdate: _updateAvailable,
        manifestUrl: '',
        downloadUrl: _updateDownloadUrl,
        sha256: _updateSha256,
        notes: _updateNotes,
      );
      final output = await _client.downloadUpdatePackage(info);
      _updatePackagePath = output;
      _message = '升级包已下载：$output';
    });
  }

  Future<void> openUpdatePackageFolder() async {
    final path = _updatePackagePath.trim();
    if (path.isEmpty) {
      _message = '\u8bf7\u5148\u4e0b\u8f7d\u5347\u7ea7\u5305\u3002';
      notifyListeners();
      return;
    }
    try {
      if (Platform.isWindows) {
        await Process.start('explorer.exe', ['/select,', path]);
      } else {
        final file = File(path);
        final parent = file.parent.path;
        await Process.start('xdg-open', [parent]);
      }
    } catch (e) {
      _errorCode = 'E-OPEN-PATH';
      _error = '\u65e0\u6cd5\u6253\u5f00\u5347\u7ea7\u5305\u76ee\u5f55\uff1a$e';
      notifyListeners();
    }
  }

  Future<void> pickPdf() async {
    _clearNotice();
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf'],
      withData: true,
    );
    if (result == null || result.files.isEmpty) {
      return;
    }
    final picked = result.files.first;
    File? file;
    if (picked.path != null && picked.path!.isNotEmpty) {
      file = File(picked.path!);
    } else if (picked.bytes != null) {
      file = await _createTempPdf(picked);
    }
    if (file == null) {
      _error = '无法读取所选 PDF 文件。';
      _errorCode = 'E-FILE-READ';
      notifyListeners();
      return;
    }
    _filePath = file.path;
    _fileName = picked.name;
    notifyListeners();
  }

  Future<void> startReview() async {
    if (_filePath.isEmpty) {
      _error = '请先选择 PDF 文件。';
      _errorCode = 'E-FILE-MISSING';
      notifyListeners();
      return;
    }

    await _runGuarded(() async {
      _stopPolling();
      _status = 'submitting';
      _progress = 0;
      _stage = 'start';
      _resultJson = null;
      _runtimeMeta = <String, dynamic>{};
      _reportPayload = null;
      _reportMarkdown = '';
      _exportedPath = '';
      _diagnosticsPath = '';
      _reviewStartedAt = DateTime.now();
      _reviewFinishedAt = null;
      _error = '';
      _errorCode = '';
      notifyListeners();

      final id = await _client.startAnalyze(File(_filePath));
      _jobId = id;
      _status = 'running';
      _stage = 'submitted';
      _progress = 1;
      _message = '任务已提交，正在处理合同。';
      _error = '';
      _errorCode = '';
      notifyListeners();
      _startPolling();
    });
  }

  Future<void> refreshResult() async {
    final id = _jobId;
    if (id == null) {
      return;
    }
    await _runGuarded(() async {
      final result = await _client.fetchResult(id);
      _status = result.status;
      _stage = result.stage;
      _progress = result.progress;
      _resultJson = result.resultJson;
      _reportPayload = result.reportPayload;
      _reportMarkdown = _selectReportText(result: result);
      _mergeRuntimeMeta(result.runtimeMeta);
      if (result.status == 'done' || result.status == 'error') {
        _reviewFinishedAt ??= DateTime.now();
      }
      if (result.error.isNotEmpty) {
        _error = result.error;
        _errorCode = 'E-REVIEW-RESULT';
      }
    });
  }

  Future<void> exportPdf() async {
    final id = _jobId;
    if (id == null) {
      _error = '请先执行审查任务。';
      _errorCode = 'E-JOB-MISSING';
      notifyListeners();
      return;
    }
    await _runGuarded(() async {
      final output = await _client.exportPdf(
        id,
        preferredName: _buildOutputName(),
      );
      _exportedPath = output;
      _message = 'PDF 已导出：$output';
    });
  }

  Future<void> exportDiagnostics() async {
    await _runGuarded(() async {
      final output = await _client.exportLogsBundle();
      _diagnosticsPath = output;
      _message = '诊断包已导出：$output';
    });
  }

  Future<void> clearAll() async {
    _stopPolling();
    _jobId = null;
    _status = 'idle';
    _progress = 0;
    _stage = '';
    _resultJson = null;
    _runtimeMeta = <String, dynamic>{};
    _reportPayload = null;
    _reportMarkdown = '';
    _exportedPath = '';
    _diagnosticsPath = '';
    _message = '';
    _error = '';
    _errorCode = '';
    _reviewStartedAt = null;
    _reviewFinishedAt = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _stopPolling();
    super.dispose();
  }

  Future<void> _runGuarded(Future<void> Function() task) async {
    _loading = true;
    notifyListeners();
    try {
      await task();
    } catch (e) {
      _applyError(e);
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  void _applyError(Object e) {
    if (e is ApiException) {
      _errorCode = e.code;
      _error = e.displayText;
      return;
    }
    _errorCode = 'E-UNKNOWN-001';
    _error = '[E-UNKNOWN-001] 系统发生未预期错误，请导出诊断包并联系支持。\n详情：$e';
  }

  void _applyPreflightFailure(Map<String, dynamic> report) {
    final code = (report['error_code'] ?? 'E-PREFLIGHT-FAILED').toString();
    final msg = (report['error_message'] ?? report['summary'] ?? '启动检查未通过').toString();
    final sb = StringBuffer('[$code] $msg');

    final suggestions = <String>[];
    final rawSuggestions = report['suggestions'];
    if (rawSuggestions is List) {
      for (final item in rawSuggestions) {
        final text = item.toString().trim();
        if (text.isNotEmpty) {
          suggestions.add(text);
        }
      }
    }
    if (suggestions.isNotEmpty) {
      sb.write('\n建议：${suggestions.take(3).join('；')}');
    }

    _errorCode = code;
    _error = sb.toString();
  }

  void _applyUpdateInfo(UpdateInfo info) {
    _updateAvailable = info.hasUpdate;
    _updateCurrentVersion = info.currentVersion;
    _updateLatestVersion = info.latestVersion;
    _updateDownloadUrl = info.downloadUrl;
    _updateSha256 = info.sha256;
    _updateNotes = info.notes;
    _updatePackagePath = '';
  }

  void _mergeRuntimeMeta(Map<String, dynamic>? meta) {
    if (meta == null || meta.isEmpty) {
      return;
    }
    _runtimeMeta = <String, dynamic>{..._runtimeMeta, ...meta};
  }

  void _startPolling() {
    _stopPolling();
    _polling = true;
    notifyListeners();
    _pollTimer = Timer.periodic(const Duration(seconds: 1), (_) async {
      await _pollOnce();
    });
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
    _polling = false;
  }

  Future<void> _pollOnce() async {
    final id = _jobId;
    if (id == null) {
      _stopPolling();
      return;
    }
    try {
      final data = await _client.fetchStatus(id);
      _status = data.status;
      _progress = data.progress;
      _stage = data.stage;
      if (data.resultJson != null) {
        _resultJson = data.resultJson;
      }
      if (data.reportPayload != null) {
        _reportPayload = data.reportPayload;
      }
      if (data.resultMarkdown.isNotEmpty) {
        _reportMarkdown = data.resultMarkdown;
      }
      _mergeRuntimeMeta(data.runtimeMeta);

      if (data.isDone || data.isError) {
        _stopPolling();
        _reviewFinishedAt ??= DateTime.now();
        final result = await _client.fetchResult(id);
        _resultJson = result.resultJson;
        _reportPayload = result.reportPayload;
        _reportMarkdown = _selectReportText(result: result);
        _mergeRuntimeMeta(result.runtimeMeta);
        if (result.error.isNotEmpty) {
          _error = result.error;
          _errorCode = 'E-REVIEW-FAILED';
        } else {
          _message = '任务已完成。';
          _errorCode = '';
        }
      }
      notifyListeners();
    } catch (e) {
      _stopPolling();
      _reviewFinishedAt ??= DateTime.now();
      _applyError(e);
      notifyListeners();
    }
  }

  Future<File> _createTempPdf(PlatformFile file) async {
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}${Platform.pathSeparator}${file.name}';
    final out = File(path);
    await out.writeAsBytes(file.bytes!, flush: true);
    return out;
  }

  String _selectReportText({required JobResult result}) {
    if (result.reportMarkdown.trim().isNotEmpty) {
      return result.reportMarkdown;
    }
    if (result.resultMarkdown.trim().isNotEmpty) {
      return result.resultMarkdown;
    }
    return '';
  }

  static String _formatDuration(Duration d) {
    final hours = d.inHours;
    final minutes = d.inMinutes.remainder(60);
    final seconds = d.inSeconds.remainder(60);
    if (hours > 0) {
      return '${hours.toString().padLeft(2, '0')}:${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
    }
    return '${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
  }

  String _buildOutputName() {
    if (_fileName.isNotEmpty) {
      final dot = _fileName.lastIndexOf('.');
      final stem = dot > 0 ? _fileName.substring(0, dot) : _fileName;
      return '${stem}_审查报告.pdf';
    }
    final id = _jobId ?? 0;
    return 'contract_review_job_$id.pdf';
  }

  void _clearNotice() {
    _message = '';
    _error = '';
    _errorCode = '';
  }

  static double? _toDouble(dynamic value) {
    if (value is num) {
      return value.toDouble();
    }
    if (value is String) {
      return double.tryParse(value);
    }
    return null;
  }

  static String _defaultBaseUrl() {
    if (!kIsWeb && Platform.isAndroid) {
      return 'http://10.0.2.2:8003/contract';
    }
    return 'http://127.0.0.1:8003/contract';
  }
}
