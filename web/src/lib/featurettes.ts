import type { DocumentaryFeaturette } from "../types";

export function featuretteQuestionLabel(featurette: Pick<DocumentaryFeaturette, "question" | "subject" | "title">) {
  const question = featurette.question.trim();
  if (question) {
    return question;
  }
  const subject = featurette.subject.trim();
  if (subject && !/^reel\s+\d+$/i.test(subject)) {
    return `What changed about ${subject.toLowerCase()} in this future?`;
  }
  const title = featurette.title.trim();
  if (title) {
    return `What does ${title.toLowerCase()} reveal about this future?`;
  }
  return "What part of this future does this reel explain?";
}
