import 'package:flutter_test/flutter_test.dart';

import 'package:contract_review_flutter/app.dart';

void main() {
  testWidgets('app renders header text', (WidgetTester tester) async {
    await tester.pumpWidget(const ContractReviewApp());

    expect(find.text('合同智能审查'), findsOneWidget);
    expect(find.text('连接与任务'), findsOneWidget);
  });
}
